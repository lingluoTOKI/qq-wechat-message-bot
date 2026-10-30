import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[2]


def method():
    node=next(n for n in ast.walk(ast.parse((ROOT/'plugins/astrbot_plugin_dafeiyu_style/main.py').read_text(encoding='utf-8'))) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_learn_group')
    ns={'asyncio':asyncio,'time':time,'logger':SimpleNamespace(info=lambda *x:None)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_multibatch','exec'),ns)
    return ns['_learn_group']


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_stops_on_budget_and_keeps_successful_counts(self):
        calls=AsyncMock(side_effect=[{'advanced':True,'added':{'rules':2}}, {'advanced':True,'added':{'rules':1}}, {'advanced':False,'error':'budget'}])
        obj=SimpleNamespace(_learning_lock=asyncio.Lock(),cfg={'max_batches_per_run':10,'run_seconds':180},_learn_batch=calls)
        result=await method()(obj,'group');self.assertEqual(result['batches'],2);self.assertEqual(result['added']['rules'],3);self.assertEqual(result['error'],'budget');self.assertEqual(calls.await_count,3)

    async def test_batch_cap_prevents_unbounded_catchup(self):
        calls=AsyncMock(return_value={'advanced':True,'added':{}})
        obj=SimpleNamespace(_learning_lock=asyncio.Lock(),cfg={'max_batches_per_run':2,'run_seconds':180},_learn_batch=calls)
        result=await method()(obj,'group');self.assertEqual(calls.await_count,2);self.assertEqual(result['error'],'batch_limit')

    async def test_time_limit_checked_before_new_batch(self):
        calls=AsyncMock()
        obj=SimpleNamespace(_learning_lock=asyncio.Lock(),cfg={'max_batches_per_run':2,'run_seconds':1},_learn_batch=calls)
        with patch('time.monotonic',side_effect=[0,2]):result=await method()(obj,'group')
        self.assertEqual(calls.await_count,0);self.assertEqual(result['error'],'time_limit')
