import asyncio
import csv
import gzip
import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import httpx

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'plugins'))
from dafeiyu_runtime.request_ledger import Ledger,NANO,check_budget
from dafeiyu_runtime.request_capture import Capture,extract_usage


def completion(identity='reply-1'):
    return {'id':identity,'usage':{'prompt_tokens':100,'prompt_cache_hit_tokens':90,
            'prompt_cache_miss_tokens':10,'completion_tokens':5},'choices':[{'message':{'content':'private response'}}]}


def export(path,*,cost='0.00003180',requests=1,comment=b'',account='account',key='masked'):
    cost_text=io.StringIO();w=csv.writer(cost_text)
    w.writerow(['user_id','start_time_iso','end_time_iso','model','name','wallet_type','cost','currency'])
    w.writerow([account,'2026-10-29T00:00:00+08:00','2026-10-29T01:00:00+08:00','deepseek-flash','deepseek-flash','Granted',cost,'CNY'])
    amount=io.StringIO();w=csv.writer(amount)
    w.writerow(['user_id','start_time_iso','end_time_iso','model','name','api_key_name','api_key','type','price','amount'])
    for kind,price,n in [('request_count','',requests),('input_cache_hit_tokens','.00000002',90),('input_cache_miss_tokens','.000001',10),('output_tokens','.000004',5)]:
        w.writerow([account,'2026-10-29T00:00:00+08:00','2026-10-29T01:00:00+08:00','deepseek-flash','deepseek-flash','name',key,kind,price,n])
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('cost.csv',cost_text.getvalue());z.writestr('amount.csv',amount.getvalue());z.comment=comment


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'requests.db';self.store=Ledger(self.db)
    def tearDown(self):self.temp.cleanup()
    def test_response_id_deduplicates_across_attempts(self):
        for _ in range(2):
            identity=self.store.start('deepseek-flash','/chat/completions','key')
            self.store.finish(identity,'usage_received',usage=(90,10,5),response_id='one')
        data=self.store.summary(0)
        self.assertEqual(data['requests'],1);self.assertEqual(data['states']['usage_received'],2)
    def test_conflicting_duplicate_usage_is_flagged(self):
        first=self.store.start('deepseek-flash','/responses','key')
        self.store.finish(first,'usage_received',usage=(90,10,5),response_id='r')
        second=self.store.start('deepseek-flash','/responses','key')
        self.store.finish(second,'usage_received',usage=(90,10,9),response_id='r')
        self.assertEqual(self.store.summary(0)['states']['usage_conflict'],1)
        self.assertEqual(self.store.summary(0)['requests'],1)
    def test_different_keys_do_not_merge_same_response_id(self):
        for key in ['a','b']:
            identity=self.store.start('deepseek-flash','/chat/completions',key)
            self.store.finish(identity,'usage_received',usage=(90,10,5),response_id='same')
        self.assertEqual(self.store.summary(0)['requests'],2)
    def test_missing_usage_never_becomes_zero_charge(self):
        identity=self.store.start('deepseek-flash','/responses')
        self.store.finish(identity,'missing_usage')
        data=self.store.summary(0)
        self.assertEqual(data['requests'],0);self.assertEqual(data['unknown_usage'],1)
    def test_unknown_model_is_unpriced_not_free(self):
        identity=self.store.start('other-model','/responses')
        self.store.finish(identity,'usage_received',usage=(90,10,5),response_id='a')
        self.assertEqual(self.store.summary(0)['unpriced'],1)
    def test_zero_and_inconsistent_usage(self):
        self.assertEqual(extract_usage({'usage':{'prompt_tokens':0,'completion_tokens':0}})[0],(0,0,0))
        data=completion();data['usage']['prompt_cache_miss_tokens']=12
        self.assertIsNone(extract_usage(data)[0])
    def test_missing_cache_breakdown_is_not_guessed(self):
        self.assertIsNone(extract_usage({'usage':{'prompt_tokens':100,'completion_tokens':5}})[0])
    def test_responses_api_usage(self):
        payload={'type':'response.completed','response':{'id':'r','usage':{'input_tokens':100,'input_tokens_details':{'cached_tokens':90},'output_tokens':5}}}
        self.assertEqual(extract_usage(payload),((90,10,5),'r'))
    def test_same_export_does_not_move_checkpoint(self):
        first=Path(self.temp.name)/'first.zip';second=Path(self.temp.name)/'second.zip'
        export(first);export(second,comment=b'new zip metadata')
        a=self.store.import_zip(first);checkpoint=self.store.summary(0)['official'][0]['baseline']
        b=self.store.import_zip(second)
        self.assertFalse(a['duplicate']);self.assertTrue(b['duplicate'])
        self.assertEqual(self.store.summary(0)['official'][0]['baseline'],checkpoint)
    def test_official_numbers_are_exact(self):
        p=Path(self.temp.name)/'official.zip';export(p,cost='1.261369',requests=114)
        result=self.store.import_zip(p)
        self.assertEqual(result['days']['2026-10-29']['cost_nano'],1261369000)
    def test_older_snapshot_rejected(self):
        first=Path(self.temp.name)/'first.zip';second=Path(self.temp.name)/'second.zip'
        export(first,cost='2',requests=4);export(second,cost='1',requests=2)
        self.store.import_zip(first)
        with self.assertRaises(ValueError):self.store.import_zip(second)
    def test_other_account_rejected(self):
        first=Path(self.temp.name)/'first.zip';second=Path(self.temp.name)/'second.zip'
        export(first);export(second,account='different')
        self.store.import_zip(first)
        with self.assertRaises(ValueError):self.store.import_zip(second)
    def test_snapshot_does_not_double_count_previous_local(self):
        from datetime import datetime
        from dafeiyu_runtime.request_ledger import TZ
        stamp=datetime(2026,10,29,10,tzinfo=TZ).timestamp()
        first=self.store.start('deepseek-flash','/responses',started=stamp-2)
        self.store.finish(first,'usage_received',usage=(90,10,5),response_id='before')
        p=Path(self.temp.name)/'official.zip'
        export(p,cost='1')
        with patch('dafeiyu_runtime.request_ledger.time.time',return_value=stamp+10):self.store.import_zip(p)
        second=self.store.start('deepseek-flash','/responses',started=stamp+20)
        self.store.finish(second,'usage_received',usage=(90,10,5),response_id='after')
        data=self.store.summary(0,end=stamp+100)
        with self.store.connect() as c:
            cost=c.execute('SELECT cost_nano FROM charges WHERE attempt_id=?',(second,)).fetchone()[0]
        self.assertEqual(data['estimated_cost_nano'],NANO+cost)
    def test_official_floor_enforces_unchanged_budget(self):
        p=Path(self.temp.name)/'official.zip';export(p,cost='1.261369');self.store.import_zip(p)
        cfg=Path(self.temp.name)/'config.json';cfg.write_text(json.dumps({'default_on_exceeded':'stop','budgets_cost':{'global_daily':1}}))
        # Explicitly pin the window to the imported official date.
        with patch('dafeiyu_runtime.usage.window_start',return_value='2026-10-28 16:00:00'):
            allowed,why=check_budget(db=self.db,config=cfg)
        self.assertFalse(allowed);self.assertIn('费用预算',why)
    def test_storage_does_not_contain_messages_or_keys(self):
        identity=self.store.start('deepseek-flash','/responses',key_hash='opaque-hash')
        self.store.finish(identity,'usage_received',usage=(90,10,5),response_id='a')
        with self.store.connect() as c:dump='\n'.join(c.iterdump())
        self.assertNotIn('private response',dump);self.assertNotIn('Authorization',dump)


class Chunks(httpx.AsyncByteStream):
    def __init__(self,data):self.data=data;self.closed=False
    async def __aiter__(self):
        for chunk in self.data:yield chunk
    async def aclose(self):self.closed=True


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Ledger(Path(self.temp.name)/'db')
        self.capture=Capture(self.store);self.capture.install()
    async def asyncTearDown(self):
        self.capture.uninstall();self.temp.cleanup()
    async def request(self,handler,**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await client.post('https://api.deepseek.com/chat/completions',json={'model':'deepseek-flash','messages':[{'role':'user','content':'private prompt'}]},headers={'Authorization':'Bearer private-key'},**kwargs)
    async def test_non_stream_counts_each_api_call(self):
        n=[0]
        def handler(r):n[0]+=1;return httpx.Response(200,json=completion(str(n[0])))
        await self.request(handler);await self.request(handler)
        self.assertEqual(self.store.summary(0)['requests'],2)
    async def test_stream_split_frames_preserves_bytes(self):
        data=('data: '+json.dumps({'id':'s','choices':[{'delta':{'content':'private response'}}]})+'\r\n\r\n'+'data: '+json.dumps(completion('s'))+'\n\ndata: [DONE]\n\n').encode()
        chunks=Chunks([data[:13],data[13:55],data[55:]])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=chunks))) as client:
            async with client.stream('POST','https://api.deepseek.com/chat/completions',json={'model':'deepseek-flash'}) as response:
                got=b''.join([b async for b in response.aiter_bytes()])
        self.assertEqual(got,data);self.assertEqual(self.store.summary(0)['requests'],1)
    async def test_gzip_sse(self):
        data=('data: '+json.dumps(completion())+'\n\n').encode();compressed=gzip.compress(data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream','content-encoding':'gzip'},stream=Chunks([compressed])))) as client:
            async with client.stream('POST','https://api.deepseek.com/responses',json={'model':'deepseek-flash'}) as response:
                self.assertEqual(await response.aread(),data)
        self.assertEqual(self.store.summary(0)['requests'],1)
    async def test_failed_attempt_then_success_separated(self):
        await self.request(lambda r:httpx.Response(500,json={'error':'upstream'}))
        await self.request(lambda r:httpx.Response(200,json=completion()))
        data=self.store.summary(0)
        self.assertEqual(data['requests'],1);self.assertEqual(data['states']['http_error'],1)
    async def test_network_error_recorded_and_propagated(self):
        def handler(r):raise httpx.ConnectError('failed',request=r)
        with self.assertRaises(httpx.ConnectError):await self.request(handler)
        self.assertEqual(self.store.summary(0)['unknown_usage'],1)
    async def test_missing_usage_not_estimated_from_text(self):
        await self.request(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'hello'}}]}))
        self.assertEqual(self.store.summary(0)['unknown_usage'],1)
    async def test_partial_stream_is_unknown(self):
        stream=Chunks([b'data: {"id":"partial","choices":[]}\n\n'])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=stream))) as client:
            async with client.stream('POST','https://api.deepseek.com/chat/completions',json={'model':'deepseek-flash'}) as response:
                async for _ in response.aiter_bytes():break
        self.assertEqual(self.store.summary(0)['unknown_usage'],1)
    async def test_other_hosts_not_tracked(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=completion()))) as client:
            await client.post('https://example.com/chat/completions',json={'model':'deepseek-flash'})
        self.assertEqual(self.store.summary(0)['states'],{})
    async def test_guard_blocks_before_network(self):
        called=[];self.capture.budget_check=lambda:(False,'limit')
        with self.assertRaises(RuntimeError):await self.request(lambda r:called.append(True))
        self.assertEqual(called,[]);self.assertEqual(self.store.summary(0)['states'],{})
    async def test_reinstall_is_not_double_capture(self):
        self.capture.install()
        await self.request(lambda r:httpx.Response(200,json=completion()))
        self.assertEqual(self.store.summary(0)['requests'],1)
    async def test_buffered_sse_is_counted(self):
        data=('data: '+json.dumps(completion())+'\n\n').encode()
        await self.request(lambda r:httpx.Response(200,content=data,headers={'content-type':'text/event-stream'}))
        self.assertEqual(self.store.summary(0)['requests'],1)
    async def test_uninstall_restores_other_wrapper(self):
        self.capture.uninstall()
        original=httpx.AsyncClient.send;seen=[]
        async def outer(client,request,*a,**kw):
            seen.append(True);return await original(client,request,*a,**kw)
        httpx.AsyncClient.send=outer
        try:
            self.capture.install()
            await self.request(lambda r:httpx.Response(200,json=completion()))
            self.capture.uninstall()
            self.assertIs(httpx.AsyncClient.send,outer);self.assertEqual(len(seen),1)
        finally:httpx.AsyncClient.send=original
