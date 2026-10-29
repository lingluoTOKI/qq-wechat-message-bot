import ast
import asyncio
import inspect
import logging
import math
import sys
import threading
import types
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, patch
from test_voice_delivery import PATCHES
sys.path.insert(0,str(PATCHES.parents[1]))
from dafeiyu_runtime.paid import guarded_model_call, ModelBudgetBlocked


def compile_nodes(path,names,ns):
    nodes=[n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*nodes],type_ignores=[])
    ast.fix_missing_locations(module);exec(compile(module,str(path),'exec'),ns);return ns


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    def load(self):
        def normalize(v):
            norm=math.sqrt(sum(x*x for x in v))
            return [x/norm for x in v]
        return compile_nodes(PATCHES/'meme_manager/backend/semantic/index.py',{'EmbeddingAdapter','close_embedding_queries'},
            {'asyncio':asyncio,'inspect':inspect,'guarded_model_call':guarded_model_call,
             'normalize_vector':normalize,'_QUERY_VECTOR_CACHE':OrderedDict(),
             'QUERY_CACHE_SIZE':128,'_QUERY_INFLIGHT':{},'QUERY_INFLIGHT_LIMIT':16})

    def provider(self):
        return types.SimpleNamespace(provider_config={'id':'test'},model='test-model',get_dim=lambda:2,
                                     get_embedding=AsyncMock(return_value=[1,0]))

    async def test_uncached_blocked_but_cache_still_usable(self):
        ns=self.load();p=self.provider();a=ns['EmbeddingAdapter'](p)
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,'spent')):
            with self.assertRaises(ModelBudgetBlocked):await a.embed('new')
            ns['_QUERY_VECTOR_CACHE'][(a.signature,'cached')]=(1.,0.)
            self.assertEqual(await a.embed('cached'),[1.,0.])
        p.get_embedding.assert_not_called()

    async def test_parallel_queries_across_adapters_share_request(self):
        ns=self.load();p=self.provider();a=ns['EmbeddingAdapter'](p);b=ns['EmbeddingAdapter'](p)
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            results=await asyncio.gather(a.embed('same'),b.embed('same'),a.embed('same'))
        self.assertEqual(results,[[1.,0.]]*3);p.get_embedding.assert_awaited_once()
        await asyncio.sleep(0);self.assertFalse(ns['_QUERY_INFLIGHT'])

    async def test_sync_provider_runs_outside_message_thread(self):
        ns=self.load();p=self.provider();threads=[];main_thread=threading.get_ident()
        def sync(text):threads.append(threading.get_ident());return [1,0]
        p.get_embedding=sync
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            self.assertEqual(await ns['EmbeddingAdapter'](p).embed('sync'),[1.,0.])
        self.assertNotEqual(threads,[main_thread])

    async def test_cancel_one_waiter_keeps_shared_query(self):
        ns=self.load();p=self.provider();started=asyncio.Event();release=asyncio.Event()
        async def run(text):started.set();await release.wait();return [1,0]
        p.get_embedding.side_effect=run;a=ns['EmbeddingAdapter'](p)
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            first=asyncio.create_task(a.embed('same'));second=asyncio.create_task(a.embed('same'))
            await started.wait();first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            release.set();self.assertEqual(await second,[1.,0.])
        p.get_embedding.assert_awaited_once()

    async def test_batch_block_count_validation_and_empty_input(self):
        ns=self.load();p=self.provider();p.get_embeddings=AsyncMock(return_value=[[1,0]])
        a=ns['EmbeddingAdapter'](p)
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,'spent')):
            self.assertEqual(await a.embed_many([]),[])
            with self.assertRaises(ModelBudgetBlocked):await a.embed_many(['a'])
        p.get_embeddings.assert_not_called()
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            with self.assertRaises(RuntimeError):await a.embed_many(['a','b'])

    async def test_failure_is_not_cached_and_can_retry(self):
        ns=self.load();p=self.provider();p.get_embedding.side_effect=[RuntimeError('network'),[1,0]]
        a=ns['EmbeddingAdapter'](p)
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            with self.assertRaises(RuntimeError):await a.embed('same')
            self.assertEqual(await a.embed('same'),[1.,0.])
        self.assertEqual(p.get_embedding.await_count,2)

    async def test_shutdown_reaps_shared_query_task(self):
        ns=self.load();p=self.provider();started=asyncio.Event()
        async def run(text):started.set();await asyncio.Event().wait()
        p.get_embedding.side_effect=run
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            waiter=asyncio.create_task(ns['EmbeddingAdapter'](p).embed('pending'))
            await started.wait();await ns['close_embedding_queries']()
            with self.assertRaises(asyncio.CancelledError):await waiter
        self.assertFalse(ns['_QUERY_INFLIGHT'])


class QueryStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_index_is_not_no_match(self):
        ns={'asyncio':asyncio,'ModelBudgetBlocked':ModelBudgetBlocked,'logger':logging.getLogger('test'),
            'load_metadata':lambda path:{'images':{}},'search_index':AsyncMock()}
        compile_nodes(PATCHES/'meme_manager/backend/semantic/query.py',{'search_memes_multi_pack'},ns)
        result=await ns['search_memes_multi_pack']([{'pack_id':'x','pack_dir':'synthetic'}],'synthetic','query',lambda p:object())
        self.assertFalse(result['ok']);self.assertIn('没有可用',result['reason'])
        ns['search_index'].assert_not_called()

    async def test_budget_or_network_failure_is_not_no_match(self):
        for error,expected in [(ModelBudgetBlocked('spent'),'预算不足'),(RuntimeError('network'),'暂不可用')]:
            ns={'asyncio':asyncio,'ModelBudgetBlocked':ModelBudgetBlocked,'logger':logging.getLogger('test'),
                'load_metadata':lambda path:{'images':{'id':{}}},'EmbeddingAdapter':lambda p:types.SimpleNamespace(provider_id='x',model_name='m',dimension=2),
                'index_is_ready':lambda *a,**kw:True,'search_index':AsyncMock(side_effect=error)}
            compile_nodes(PATCHES/'meme_manager/backend/semantic/query.py',{'search_memes_multi_pack'},ns)
            result=await ns['search_memes_multi_pack']([{'pack_id':'x','pack_dir':'synthetic'}],'synthetic','query',lambda p:object())
            self.assertFalse(result['ok']);self.assertIn(expected,result['reason'])
