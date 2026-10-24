"""可暂停的历史回顾与人工选中文件转存；不自动运行全量任务。"""
import asyncio
import hashlib
import json
import time
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import aiohttp
from aiohttp import web
from vault import seal,unseal
from workflow_plus import WorkflowPlus,fingerprint

TARGET_GROUP=917905372

def encode(value):return seal(json.dumps(value,ensure_ascii=False))
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def wechat_page(source=None,offset=0):
    from wechat_listener import Listener
    listener=Listener()
    try:
        listener.connect();reader=listener.reader
        if source is None:
            return [dict(platform='wechat',kind='group' if c['username'].endswith('@chatroom') else 'private',source=c['username'],label=reader.get_nickname(c['username']) or c['username']) for c in reader.get_sessions(limit=10000) if not c['username'].startswith(('gh_','weixin','filehelper')) and c['username']!=reader.wxid]
        rows=reader.get_messages(source,limit=50,offset=offset)
        return rows,str(Path(reader.account_dir)/'msg'/'file')
    finally:listener.db.close()

def wechat_file(row,base):
    if row.get('type')!='文件/链接/卡片':return None
    content=str(row.get('content',''))
    if '<!DOCTYPE' in content or '<!ENTITY' in content:return None
    try:
        xml=ET.fromstring(content[content.index('<'):])
        app=xml if xml.tag=='appmsg' else xml.find('.//appmsg')
        if app is None or app.findtext('type')!='6':return None
        name=app.findtext('title') or '';size=int(app.findtext('appattach/totallen') or 0)
        if not name or Path(name).name!=name or re.search(r'[\\/:\x00]',name):return None
        month=datetime.fromtimestamp(float(row['create_time']),ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
        root=Path(base).resolve();folder=(root/month).resolve()
        # Only exact original-name matches; never guess among different versions.
        candidate=folder/name
        candidates=[candidate] if candidate.is_file() and candidate.resolve().parent==folder and (not size or candidate.stat().st_size==size) else []
        return dict(name=name,size=size,path=str(candidates[0]) if len(candidates)==1 else '',base=str(root),available=len(candidates)==1,reason='' if len(candidates)==1 else '本机未找到唯一匹配原文件，请先在微信中下载')
    except (ValueError,TypeError,ET.ParseError,OSError):return None

class FullReviewStore(WorkflowPlus):
    def init_full_review(self):
        self.db.executescript('''CREATE TABLE IF NOT EXISTS full_review(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS full_review_seen(id TEXT PRIMARY KEY,at REAL);
        CREATE TABLE IF NOT EXISTS file_candidates(id TEXT PRIMARY KEY,payload TEXT,status TEXT,at REAL);''')
        self.init_plus()
        row=self.db.execute("SELECT payload FROM full_review WHERE id='current'").fetchone()
        self.full_job=json.loads(unseal(row[0])) if row else dict(state='idle')
        if self.full_job.get('state')=='running':self.full_job.update(state='paused',stage='程序重启，点击继续回顾')
        self.full_task=None;self.file_task=None
        self.db.execute("UPDATE file_candidates SET status='uncertain' WHERE status='sending'")
        self.save_full_job()

    def save_full_job(self):
        self.db.execute("INSERT OR REPLACE INTO full_review VALUES('current',?)",(encode(self.full_job),));self.db.commit()

    def full_review_data(self,offset=0,filters=None):
        files=[]
        filters=filters or {};count=0;target=str(self.cfg.get('target_group',TARGET_GROUP))
        filtered=bool(filters.get('q') or filters.get('platform') in ('qq','wechat') or float(filters.get('start') or 0)>0 or float(filters.get('end') or 4102444800)<4102444800)
        if filtered:rows=self.db.execute('SELECT id,payload,status,at FROM file_candidates ORDER BY at DESC,id')
        else:
            rows=self.db.execute('SELECT id,payload,status,at FROM file_candidates ORDER BY at DESC,id LIMIT 200 OFFSET ?',(offset,));count=offset
        for key,blob,status,at in rows:
            value=json.loads(unseal(blob));stamp=value.get('message_time',at)
            if filters.get('platform') in ('qq','wechat') and value.get('platform')!=filters['platform']:continue
            if filters.get('q','').casefold() not in (value.get('name','')+' '+value.get('source_name','')).casefold():continue
            if not float(filters.get('start') or 0)<=stamp<=float(filters.get('end') or 4102444800):continue
            count+=1
            if not offset<count<=offset+200:continue
            prior=self.db.execute("SELECT status FROM content_uploads WHERE hash=? AND target=?",(value.get('content_hash',''),target)).fetchone()
            files.append({k:value.get(k) for k in ('name','platform','source_name','size','available','reason','hint','hash_note','message_time','upload_target')}|dict(id=key,status=status,at=at,duplicate=bool(prior and prior[0] in ('sent','confirmed')),hash_uncertain=bool(prior and prior[0]=='uncertain')))
        job={k:v for k,v in self.full_job.items() if k not in ('sources','page_keys','failed_sources')}
        job['failed_count']=len(self.full_job.get('failed_sources',[]))
        if not filtered:count=self.db.execute('SELECT count(*) FROM file_candidates').fetchone()[0]
        return dict(job=job,files=files,offset=offset,target_group=int(target),file_count=count)

    def full_review_action(self,value):
        action=value.get('action')
        if action not in ('full_start','full_resume','full_pause','transfer_files'):return False
        if action=='transfer_files':
            ids=value.get('files')
            if not isinstance(ids,list) or not 1<=len(ids)<=10 or any(not isinstance(k,str) for k in ids):raise web.HTTPBadRequest(text='一次选择1至10个文件')
            if not self.account_verified:raise web.HTTPConflict(text='QQ未连接')
            if self.file_task and not self.file_task.done():raise web.HTTPConflict(text='已有文件转存任务')
            for key in ids:
                row=self.db.execute('SELECT status FROM file_candidates WHERE id=?',(key,)).fetchone()
                if not row or row[0] in ('sent','confirmed','duplicate','sending','uncertain'):raise web.HTTPConflict(text='文件不存在、已发送或送达待确认，请检查群文件')
            target=int(self.cfg.get('target_group',TARGET_GROUP))
            if str(value.get('expected_group',''))!=str(target):raise web.HTTPConflict(text='保存群已变更，请刷新后重新确认')
            self.file_task=asyncio.create_task(self.transfer_files(list(dict.fromkeys(ids)),target));return True
        if action=='full_pause':
            self.full_job['pause_requested']=True;self.save_full_job();return True
        if self.full_task and not self.full_task.done():raise web.HTTPConflict(text='已有历史回顾任务')
        if getattr(self,'history_task',None) and not self.history_task.done():raise web.HTTPConflict(text='请先结束当前短期摘要')
        if action=='full_start':
            if self.full_job.get('state')=='paused':raise web.HTTPConflict(text='上次任务尚未完成，请先继续')
            scope=self.validate_scope(value.get('scope',{}))
            self.full_job=dict(state='running',sources=[],source_index=0,offset=0,anchor=0,pages=0,messages=0,reused=0,matters=0,errors=[],stage='准备读取会话',started_at=time.time(),oldest=None)
            self.full_job['scope']=scope
            self.full_job.update(failed_sources=[],scanned_records=0)
        elif not self.full_job.get('sources') and self.full_job.get('state')!='paused':raise web.HTTPConflict(text='没有可继续任务')
        self.full_job.update(state='running',pause_requested=False,segment_start=time.time(),segment_tokens=0,stage='准备继续')
        self.save_full_job();self.full_task=asyncio.create_task(self.run_full_review());return True

    def remember_file(self,group,message_id,info):
        identity=info.get('file_id') or ([info['path'],info.get('size')] if info.get('path') else message_id)
        key=digest([group['platform'],group['source'],identity,info['name']])
        previous=self.db.execute('SELECT payload FROM file_candidates WHERE id=?',(key,)).fetchone()
        if previous:info={**json.loads(unseal(previous[0])),**info}
        info.update(platform=group['platform'],source=group['source'],source_name=group['label'],kind=group['kind'])
        info['hint']='文件名可能相关，请结合来源确认' if re.search('考研|报名|通知|统计|材料|评优|评奖|思政|考试|招生',info['name']) else '待人工判断'
        self.db.execute('INSERT INTO file_candidates VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,encode(info),'candidate',time.time()));self.db.commit()

    async def discover_sources(self,platform='all',kind_filter='all'):
        sources=[];errors=[]
        if self.account_verified and platform in ('all','qq'):
            for action,kind,id_key,name_key in [('get_group_list','group','group_id','group_name'),('get_friend_list','private','user_id','nickname')]:
                if kind_filter not in ('all',kind):continue
                try:
                    rows=await self.rpc(action,{})
                    if not isinstance(rows,list):raise ValueError('list unavailable')
                    sources.extend(dict(platform='qq',kind=kind,source=str(r[id_key]),label=str(r.get(name_key) or r[id_key])) for r in rows if str(r[id_key])!=self.cfg['qq_account'])
                except Exception:errors.append('QQ'+('群聊' if kind=='group' else '好友')+'列表读取失败')
        elif platform in ('all','qq'):errors.append('QQ未连接，未读取QQ历史')
        if platform in ('all','wechat'):
            try:sources.extend(await asyncio.wait_for(asyncio.to_thread(wechat_page),60))
            except Exception:errors.append('微信会话列表读取失败，请确认微信已登录')
        return sources,errors

    async def list_full_sources(self):
        scope=self.full_job.get('scope',self.validate_scope({'selection':'all'}))
        sources,errors=await self.discover_sources(scope['platform'],scope['kind'])
        self.full_job['errors'].extend(errors)
        sources=self.select_sources(sources,self.full_job.get('scope',self.validate_scope({'selection':'all'})))
        self.full_job['sources']=sources;self.full_job['total_sources']=len(sources)

    async def read_full_page(self,group):
        job=self.full_job;messages=[];keys=[];next_anchor=0
        if group['platform']=='qq':
            action='get_group_msg_history' if group['kind']=='group' else 'get_friend_msg_history'
            param='group_id' if group['kind']=='group' else 'user_id'
            result=await self.rpc(action,{param:int(group['source']),'message_id':job['anchor'],'count':50,'reverse_order':True})
            rows=result.get('messages',[])
            for row in rows:
                message_id=row.get('message_id');stamp=float(row.get('time') or 0)
                if not message_id:continue
                key=str(message_id);parts=[]
                keys.append(key)
                if not self.in_scan_range(stamp):continue
                for segment in row.get('message',[]) if isinstance(row.get('message'),list) else []:
                    data=segment.get('data',{})
                    if segment.get('type')=='text':parts.append(str(data.get('text','')))
                    if segment.get('type')=='file':
                        name=str(data.get('name') or data.get('file_name') or '未命名文件')[:250]
                        file_id=data.get('file_id')
                        self.remember_file(group,key,dict(name=name,message_time=stamp,size=data.get('file_size') or data.get('size') or 0,file_id=file_id,file_hash=data.get('file_hash',''),available=bool(file_id),reason='' if file_id else '消息未返回文件标识',busid=data.get('busid',102)))
                        parts.append('[文件] '+name)
                text='\n'.join(parts) or (re.sub(r'\[CQ:[^\]]*\]','',row['raw_message']).strip() if isinstance(row.get('raw_message'),str) else '')
                if text:messages.append(dict(sender=str(row.get('user_id','')),text=text[:2000],time=stamp))
            if rows:
                oldest=min(rows,key=lambda r:float(r.get('time') or 0));next_anchor=oldest.get('message_id') or 0
            end=not rows or not next_anchor or str(next_anchor)==str(job['anchor'])
            if rows and min(float(r.get('time') or 0) for r in rows)<job.get('scope',{}).get('start',0):end=True
        else:
            rows,base=await asyncio.wait_for(asyncio.to_thread(wechat_page,group['source'],job['offset']),60)
            for row in rows:
                key=str(row.get('shard'))+':'+str(row['local_id']);keys.append(key)
                if not self.in_scan_range(float(row.get('create_time') or 0)):continue
                info=wechat_file(row,base)
                if info:self.remember_file(group,key,dict(info,message_time=float(row.get('create_time') or 0)))
                text=str(row.get('content','')) if row.get('type')=='文本' else '[文件] '+info['name'] if info else ''
                if text:messages.append(dict(sender=str(row.get('sender_username','')),text=text[:2000],time=float(row.get('create_time') or 0)))
            end=not rows
            if rows and min(float(r.get('create_time') or 0) for r in rows)<job.get('scope',{}).get('start',0):end=True
        messages.sort(key=lambda m:m['time'])
        return messages,keys,next_anchor,end

    async def run_full_review(self):
        job=self.full_job;pages_this_run=0
        try:
            if not job['sources']:await self.list_full_sources()
            async with aiohttp.ClientSession() as session:
                while job['source_index']<len(job['sources']):
                    if job.get('stop_requested'):job.update(state='cancelled',stage='任务已结束，已提取内容保留');break
                    if job.get('skip_index')==job['source_index']:
                        job['source_index']+=1;job.update(anchor=0,offset=0,page_keys=None,skip_index=None);continue
                    if job.get('pause_requested') or pages_this_run>=100:
                        job.update(state='paused',stage='本轮已暂停，可继续');break
                    group=dict(job['sources'][job['source_index']]);group['id']=group['platform']+':'+group['kind']+':'+group['source']
                    job['stage']='读取 '+group['label'];self.save_full_job()
                    try:messages,keys,next_anchor,end=await self.read_full_page(group)
                    except Exception as error:
                        job.setdefault('failed_sources',[]).append(job['sources'][job['source_index']])
                        job['errors']=(job['errors']+[group['platform']+' '+group['label']+'：历史接口未能继续（'+type(error).__name__+'）'])[-100:]
                        job['source_index']+=1;job.update(anchor=0,offset=0,page_keys=None);continue
                    # Repeated anchors must not make an infinite scan.
                    signature=digest(keys)
                    if keys and signature==job.get('page_keys'):end=True;messages=[]
                    group['messages']=messages
                    job['scanned_records']=job.get('scanned_records',0)+len(keys)
                    rule=self.cache_identity(group,True)
                    fresh=[];seen_keys=[]
                    for m in messages:
                        k=digest([rule,m]);seen_keys.append(k)
                        if self.db.execute('SELECT 1 FROM full_review_seen WHERE id=?',(k,)).fetchone():job['reused']+=1
                        elif job.get('scope',{}).get('mode')!='files':fresh.append(m)
                    # Page input remains bounded; process all text in slices, checkpoint after each.
                    chunks=[];chunk=[];size=0
                    for m in fresh:
                        if chunk and size+len(m['text'])>6000:chunks.append(chunk);chunk=[];size=0
                        chunk.append(m);size+=len(m['text'])
                    if chunk:chunks.append(chunk)
                    paused=False
                    for chunk in chunks:
                        if job.get('stop_requested') or job.get('skip_index')==job['source_index']:
                            paused=True;break
                        estimated=len(json.dumps(chunk,ensure_ascii=False).encode())+len(json.dumps(self.cfg['profile'],ensure_ascii=False).encode())+self.cfg['max_output_tokens']+10000
                        if job['segment_tokens']+estimated>100000 or job.get('pause_requested'):
                            job.update(state='paused',stage='本轮用量保护暂停，点击继续可开始下一轮');paused=True;break
                        # Live time-sensitive work takes priority at every chunk boundary.
                        await self.batch(session,urgent_only=True);await self.deliver()
                        job['stage']='等待模型空位 · '+group['label'];self.save_full_job()
                        async with self.lock:
                            job['stage']='分析 · '+group['label'];self.save_full_job()
                            before=self.db.execute('SELECT coalesce(sum(tokens),0) FROM token_ledger').fetchone()[0]
                            try:result=await self.analyze(session,[dict(group,messages=chunk)],history=True)
                            finally:job['segment_tokens']+=max(0,self.db.execute('SELECT coalesce(sum(tokens),0) FROM token_ledger').fetchone()[0]-before)
                        if result is None:job.update(state='paused',stage='赠送用量保护暂停');paused=True;break
                        for item in result.get('items',[]):
                            for topic in item.get('topics',[]):
                                if topic.get('relevant') and any(topic.get(k) for k in ('urgent','important','useful','reference')):
                                    self.store_matter(group,topic,origin='full_history');job['matters']+=1
                        with self.db:self.db.executemany('INSERT OR IGNORE INTO full_review_seen VALUES(?,?)',[(digest([rule,m]),time.time()) for m in chunk])
                        self.save_full_job()
                    if paused:
                        if job.get('stop_requested') or job.get('skip_index')==job['source_index']:continue
                        break
                    job['messages']+=len(messages);job['pages']+=1;pages_this_run+=1;job['page_keys']=signature
                    if messages:job['oldest']=min(job.get('oldest') or time.time(),messages[0]['time'])
                    if end:job['source_index']+=1;job.update(anchor=0,offset=0,page_keys=None)
                    else:job['anchor']=next_anchor;job['offset']+=50
                    self.save_full_job();await asyncio.sleep(.2)
                else:job.update(state='cancelled' if job.get('stop_requested') else 'partial' if job['errors'] else 'done',stage='任务已结束，已提取内容保留' if job.get('stop_requested') else '本轮可访问范围回顾完成；不代表账号全部历史均可取回')
        except asyncio.CancelledError:job.update(state='paused',stage='已暂停')
        except Exception as error:
            if job.get('sources') and job['source_index']<len(job['sources']):
                failed=job['sources'][job['source_index']]
                if failed not in job.setdefault('failed_sources',[]):job['failed_sources'].append(failed)
            job.update(state='paused',stage='回顾异常：'+type(error).__name__+'，已保存进度')
        finally:self.save_full_job()

    async def transfer_files(self,ids,target=None):
        target=int(self.cfg.get('target_group',TARGET_GROUP)) if target is None else target
        for key in ids:
            row=self.db.execute('SELECT payload,status FROM file_candidates WHERE id=?',(key,)).fetchone()
            if not row or row[1] in ('sent','confirmed','duplicate','sending','uncertain'):continue
            info=json.loads(unseal(row[0]));sending=False;attempt=digest([key,time.time_ns()]);at=time.time()
            audit=dict(name=info['name'],target=str(target),source=info.get('source_name',''),platform=info['platform'],reason='')
            self.db.execute('INSERT INTO file_audit VALUES(?,?,?,?,?)',(attempt,key,encode(audit),'preparing',at));self.db.commit()
            try:
                if info['platform']=='wechat':
                    path=Path(info.get('path') or '').resolve();base=Path(info['base']).resolve()
                    if not path.is_file() or base not in path.parents or path.name!=info['name'] or info.get('size') and path.stat().st_size!=info['size']:raise ValueError('原文件未找到或已变化')
                    stat=path.stat();info['content_hash']=await asyncio.to_thread(fingerprint,path)
                    if (stat.st_size,stat.st_mtime_ns)!=(path.stat().st_size,path.stat().st_mtime_ns):raise ValueError('文件读取期间已变化')
                    prior=self.db.execute('SELECT status FROM content_uploads WHERE hash=? AND target=?',(info['content_hash'],str(target))).fetchone()
                    if prior and prior[0] in ('sent','confirmed','uncertain','sending'):
                        state='duplicate' if prior[0] in ('sent','confirmed') else 'uncertain';info['reason']='该群已保存相同内容' if state=='duplicate' else '相同内容的上传结果待确认，请先检查目标群'
                        info['upload_target']=target;audit['reason']=info['reason']
                        with self.db:
                            self.db.execute('UPDATE file_candidates SET payload=?,status=? WHERE id=?',(encode(info),state,key))
                            self.db.execute('UPDATE file_audit SET payload=?,status=? WHERE id=?',(encode(audit),state,attempt))
                        continue
                    file=str(path)
                else:
                    if not info.get('file_id'):raise ValueError('文件标识缺失')
                    params={'file_id':info['file_id']}
                    if info['kind']=='group':params.update(group_id=int(info['source']),busid=info.get('busid',102));action='get_group_file_url'
                    else:params.update(user_id=int(info['source']),file_hash=info.get('file_hash',''));action='get_private_file_url'
                    reply=await self.rpc(action,params);file=reply.get('url','');url=urlparse(file)
                    if url.scheme not in ('http','https') or not url.hostname or not (url.hostname.endswith('.qq.com') or url.hostname.endswith('.qpic.cn') or url.hostname.endswith('.gtimg.cn')):raise ValueError('下载地址未通过校验')
                self.db.execute("UPDATE file_candidates SET status='sending' WHERE id=?",(key,));self.db.commit();sending=True
                info['upload_target']=target
                self.db.execute("UPDATE file_audit SET status='sending' WHERE id=?",(attempt,))
                if info.get('content_hash'):self.db.execute('INSERT OR REPLACE INTO content_uploads VALUES(?,?,?,?)',(info['content_hash'],str(target),'uncertain',''))
                self.db.execute('UPDATE file_candidates SET payload=? WHERE id=?',(encode(info),key));self.db.commit()
                result=await self.rpc('upload_group_file',{'group_id':target,'file':file,'name':Path(info['name']).name,'folder':'/'})
                if not result or not result.get('file_id'):raise RuntimeError('未获得群文件回执')
                info.update(reason='QQ群'+str(target)+'文件接口返回文件编号',saved_file_id=result['file_id'])
                state='sent'
            except Exception as error:
                from reliability import DeliveryRejected
                uncertain=sending and not isinstance(error,DeliveryRejected)
                info['reason']=('上传结果待确认，请先查看群文件，避免重复转存' if uncertain else '文件获取或上传被拒绝：'+type(error).__name__)
                state='uncertain' if uncertain else 'failed'
            self.db.execute('UPDATE file_candidates SET payload=?,status=? WHERE id=?',(encode(info),state,key));self.db.commit()
            audit.update(reason=info.get('reason',''),receipt=info.get('saved_file_id',''),updated=time.time())
            with self.db:
                self.db.execute('UPDATE file_audit SET payload=?,status=? WHERE id=?',(encode(audit),state,attempt))
                if info.get('content_hash'):self.db.execute('INSERT OR REPLACE INTO content_uploads VALUES(?,?,?,?)',(info['content_hash'],str(target),state,info.get('saved_file_id','')))
