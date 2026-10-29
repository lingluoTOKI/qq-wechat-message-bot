import ast
import asyncio
import json
import logging
import tempfile
import time
import types
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from test_voice_delivery import extract, PATCHES
sys.path.insert(0,str(PATCHES.parents[1]))
from dafeiyu_runtime.delivery import send_text

MAIN=PATCHES/'astrbot_plugin_bili_dynamic/main.py'


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    def load(self,path,name):
        return extract(path,name,{'asyncio':asyncio,'time':time,'logger':logging.getLogger('test')})

    def plugin(self):
        store=types.SimpleNamespace(data={},save=AsyncMock(return_value=True),subscribers_of=lambda mid:['good','bad'])
        obj=types.SimpleNamespace(store=store,_closing=False,_last_error='',_send_markdown=AsyncMock())
        for name in ['_save_deliveries','_queue_delivery','_retry_deliveries']:
            setattr(obj,name,types.MethodType(self.load(MAIN,name),obj))
        return obj

    async def test_only_failed_target_retried_and_success_survives_reload(self):
        obj=self.plugin()
        for target in ['good','bad']:obj._queue_delivery('1','2',target,'synthetic')
        async def send(target,text):
            if target=='bad':raise RuntimeError('offline')
            return 'receipt'
        obj._send_markdown.side_effect=send
        with patch.object(asyncio,'sleep',new=AsyncMock()):await obj._retry_deliveries()
        rows=obj.store.data['deliveries'];self.assertEqual(rows['1:2:good']['state'],'sent')
        self.assertEqual(rows['1:2:bad']['state'],'pending')
        next_plugin=self.plugin();next_plugin.store.data=json.loads(json.dumps(obj.store.data))
        next_plugin.store.data['deliveries']['1:2:bad']['next_at']=0
        next_plugin._send_markdown.return_value='receipt-2'
        with patch.object(asyncio,'sleep',new=AsyncMock()):await next_plugin._retry_deliveries()
        next_plugin._send_markdown.assert_awaited_once_with('bad','synthetic')

    async def test_three_attempt_limit_and_unsubscribe_cancel(self):
        obj=self.plugin();obj._queue_delivery('1','2','bad','synthetic')
        row=obj.store.data['deliveries']['1:2:bad'];obj._send_markdown.side_effect=RuntimeError('offline')
        with patch.object(asyncio,'sleep',new=AsyncMock()):
            for _ in range(5):row['next_at']=0;await obj._retry_deliveries()
        self.assertEqual(row['attempts'],3);self.assertEqual(row['state'],'failed')
        obj._queue_delivery('1','3','removed','synthetic')
        await obj._retry_deliveries()
        self.assertEqual(obj.store.data['deliveries']['1:3:removed']['state'],'cancelled')

    async def test_save_failure_prevents_send(self):
        obj=self.plugin();obj._queue_delivery('1','2','good','synthetic');obj.store.save.return_value=False
        with self.assertRaises(RuntimeError):await obj._retry_deliveries()
        obj._send_markdown.assert_not_called()

    async def test_terminate_reaps_manual_check_before_closing_api(self):
        task=asyncio.create_task(asyncio.Event().wait())
        obj=types.SimpleNamespace(_closing=False,_boot_task=None,_poll_task=None,_manual_tasks={task},api=types.SimpleNamespace(close=AsyncMock()))
        await self.load(MAIN,'terminate')(obj)
        self.assertTrue(task.done());self.assertFalse(obj._manual_tasks);obj.api.close.assert_awaited_once()

    async def test_pending_records_persist_in_real_store(self):
        path=PATCHES/'astrbot_plugin_bili_dynamic/store.py'
        node=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef))
        ns={'asyncio':asyncio,'json':json,'time':time,'Path':Path,'logger':logging.getLogger('test')}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        with tempfile.TemporaryDirectory() as tmp:
            store=ns['SubStore'](Path(tmp)/'state.json');store.data['deliveries']={'x':{'state':'pending'}}
            self.assertTrue(await store.save())
            other=ns['SubStore'](store.path);await other.load()
            self.assertEqual(other.data['deliveries'],store.data['deliveries'])

    async def test_markdown_requires_receipt_and_omits_cached_reply_id(self):
        api=types.SimpleNamespace(post_group_message=AsyncMock(return_value={'id':'receipt'}))
        platform=types.SimpleNamespace(client=types.SimpleNamespace(api=api),meta=lambda:types.SimpleNamespace(name='qq_official'))
        context=types.SimpleNamespace(get_platform_inst=lambda platform_id:platform)
        self.assertEqual(await send_text(context,'qq:GroupMessage:test','synthetic',markdown=True),'receipt')
        kwargs=api.post_group_message.call_args.kwargs
        self.assertEqual(kwargs['msg_type'],2);self.assertEqual(kwargs['markdown'],{'content':'synthetic'})
        self.assertNotIn('msg_id',kwargs)
        api.post_group_message.return_value={}
        with self.assertRaises(RuntimeError):await send_text(context,'qq:GroupMessage:test','synthetic',markdown=True)

    async def test_manual_check_error_is_observed(self):
        async def fail():raise RuntimeError('synthetic failure')
        task=asyncio.create_task(fail())
        await asyncio.sleep(0)
        obj=types.SimpleNamespace(_manual_tasks={task},_last_error='')
        self.load(MAIN,'_manual_done')(obj,task)
        self.assertFalse(obj._manual_tasks);self.assertIn('RuntimeError',obj._last_error)
