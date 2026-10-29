"""Observe HTTPX response usage without changing bytes delivered to the SDK."""
import asyncio
import hashlib
import hmac
import json
import secrets
import zlib
from pathlib import Path
import httpx

from .request_ledger import Ledger


def extract_usage(payload):
    if not isinstance(payload,dict):return None, ''
    data=payload.get('response') if isinstance(payload.get('response'),dict) else payload
    u=data.get('usage');identity=data.get('id') or ''
    if not isinstance(u,dict):return None, str(identity)
    total=u.get('prompt_tokens',u.get('input_tokens'))
    output=u.get('completion_tokens',u.get('output_tokens'))
    details=u.get('prompt_tokens_details') or u.get('input_tokens_details') or {}
    hit=u.get('prompt_cache_hit_tokens',details.get('cached_tokens'))
    if hit is None and type(total) is int and type(u.get('prompt_cache_miss_tokens')) is int:
        hit=total-u['prompt_cache_miss_tokens']
    if hit is None and total==0:hit=0
    miss=u.get('prompt_cache_miss_tokens',total-hit if type(total) is int and type(hit) is int else None)
    values=(hit,miss,output)
    if any(type(v) is not int or v<0 for v in values):return None,str(identity)
    if type(total) is int and hit+miss!=total:return None,str(identity)
    return values,str(identity)


class Parser:
    def __init__(self):
        self.pending=b'';self.usage=None;self.response_id='';self.overflow=False
    def payload(self,payload):
        usage,identity=extract_usage(payload)
        if identity:self.response_id=identity
        if usage is not None:self.usage=usage
    def feed(self,chunk):
        self.pending+=chunk
        while b'\n' in self.pending:
            line,self.pending=self.pending.split(b'\n',1)
            self.line(line.rstrip(b'\r'))
        if len(self.pending)>1024*1024:
            self.pending=b'';self.overflow=True
    def line(self,line):
        if not line.startswith(b'data:'):return
        content=line[5:].strip()
        if content==b'[DONE]':return
        try:self.payload(json.loads(content))
        except (ValueError,UnicodeError):pass
    def end(self):
        if self.pending:self.line(self.pending)
        self.pending=b''


class UsageStream(httpx.AsyncByteStream):
    def __init__(self,original,owner,identity,response):
        self.original=original;self.owner=owner;self.identity=identity
        self.response=response;self.parser=Parser();self.saved=False
        self.sse='text/event-stream' in response.headers.get('content-type','')
        self.buffer=bytearray();self.eof=False
        encoding=response.headers.get('content-encoding','').lower()
        self.decoder=zlib.decompressobj(16+zlib.MAX_WBITS) if encoding=='gzip' else zlib.decompressobj() if encoding=='deflate' else None
        self.supported=encoding in {'','identity','gzip','deflate'}
    def consume(self,data):
        if not self.supported:return
        try:
            decoded=self.decoder.decompress(data) if self.decoder else data
            if self.sse:self.parser.feed(decoded)
            elif len(self.buffer)+len(decoded)<=8*1024*1024:self.buffer.extend(decoded)
            else:self.supported=False
        except zlib.error:self.supported=False
    async def __aiter__(self):
        try:
            async for chunk in self.original:
                self.consume(chunk)
                yield chunk
            self.eof=True
        finally:
            await asyncio.shield(self.save())
    async def save(self):
        if self.saved:return
        self.saved=True
        try:
            if self.decoder and self.supported:
                data=self.decoder.flush()
                if self.sse:self.parser.feed(data)
                else:self.buffer.extend(data)
            if self.sse:self.parser.end()
            elif self.supported:
                try:self.parser.payload(json.loads(self.buffer))
                except (ValueError,UnicodeError):pass
            status='usage_received' if self.parser.usage is not None else 'missing_usage' if self.eof else 'stream_incomplete'
            await self.owner.finish(self.identity,status,usage=self.parser.usage,response_id=self.parser.response_id,
                                    request_id=self.response.headers.get('x-request-id',''),http_status=self.response.status_code)
        except Exception:
            self.owner.errors+=1
    async def aclose(self):
        try:await self.original.aclose()
        finally:await asyncio.shield(self.save())


class Capture:
    def __init__(self,ledger=None,allowed_hosts=None):
        self.ledger=ledger or Ledger();self.enabled=False;self.errors=0
        self.allowed_hosts=set(allowed_hosts or ['api.deepseek.com'])
        salt=Path(self.ledger.path).with_suffix('.salt')
        try:self.salt=salt.read_bytes()
        except FileNotFoundError:
            value=secrets.token_bytes(32)
            try:
                with salt.open('xb') as f:f.write(value)
                salt.chmod(0o600)
                self.salt=value
            except FileExistsError:self.salt=salt.read_bytes()
        self.previous=None;self.wrapper=None
        self.budget_check=None
    async def finish(self,*args,**kwargs):
        try:await asyncio.to_thread(self.ledger.finish,*args,**kwargs)
        except Exception:self.errors+=1
    def install(self):
        if self.enabled:return
        import time
        with self.ledger.connect() as c:
            if not c.execute("SELECT v FROM meta WHERE k='capture_started'").fetchone():
                started=str(time.time())
                c.execute("UPDATE meta SET v=? WHERE k='tracking_started'",(started,))
                c.execute("INSERT INTO meta VALUES('capture_started',?)",(started,))
        previous=httpx.AsyncClient.send
        # Respect wrappers installed by CacheGuard or other plugins.
        old=getattr(previous,'_dafeiyu_request_capture',None)
        if old is not None:
            old.enabled=False;previous=old.previous
        self.previous=previous;self.enabled=True;owner=self
        async def send(client,request,*args,**kwargs):
            track=(owner.enabled and request.method=='POST' and request.url.host in owner.allowed_hosts
                   and (request.url.path.endswith('/chat/completions') or request.url.path.endswith('/responses')))
            if not track:return await previous(client,request,*args,**kwargs)
            if owner.budget_check is not None:
                # Reject locally before creating an HTTP attempt. It must not
                # appear as a provider request or create retry-side charges.
                allowed,reason=await asyncio.to_thread(owner.budget_check)
                if not allowed:raise RuntimeError('模型预算已用完：'+reason)
            identity=None
            try:
                model='unknown'
                try:model=json.loads(request.content).get('model','unknown')
                except (ValueError,httpx.RequestNotRead):pass
                fingerprint=hmac.new(owner.salt,request.headers.get('authorization','').encode(),hashlib.sha256).hexdigest()
                identity=await asyncio.to_thread(owner.ledger.start,model,request.url.path,fingerprint)
            except Exception:owner.errors+=1
            try:response=await previous(client,request,*args,**kwargs)
            except BaseException:
                if identity:await asyncio.shield(owner.finish(identity,'network_error'))
                raise
            if not identity:return response
            if not 200<=response.status_code<300:
                await owner.finish(identity,'http_error',http_status=response.status_code,
                                   request_id=response.headers.get('x-request-id',''))
            elif response.is_stream_consumed:
                parser=Parser()
                try:
                    if 'text/event-stream' in response.headers.get('content-type',''):
                        parser.feed(response.content);parser.end()
                    else:parser.payload(response.json())
                except (ValueError,UnicodeError):pass
                await owner.finish(identity,'usage_received' if parser.usage is not None else 'missing_usage',
                                   usage=parser.usage,response_id=parser.response_id,
                                   request_id=response.headers.get('x-request-id',''),http_status=response.status_code)
            else:
                response.stream=UsageStream(response.stream,owner,identity,response)
            return response
        send._dafeiyu_request_capture=self
        self.wrapper=send;httpx.AsyncClient.send=send
    def uninstall(self):
        self.enabled=False
        if httpx.AsyncClient.send is self.wrapper:httpx.AsyncClient.send=self.previous
