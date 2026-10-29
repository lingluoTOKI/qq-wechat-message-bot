"""Isolated regression cases; no QQ sends, paid calls, or production writes."""
import ast
import asyncio
import calendar
import importlib.util
import json
import logging
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, date, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'plugins'))
from dafeiyu_runtime import usage, memory
from dafeiyu_runtime.context import read_once
from dafeiyu_runtime.delivery import send_text


def function(path, name, globals_):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    # Remove annotations that refer to the plugin framework.
    node.returns = None
    for arg in node.args.args + node.args.kwonlyargs: arg.annotation = None
    namespace = dict(globals_)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name)/'ledger.db'
        self.cfg = Path(self.temp.name)/'config.json'
        self.c = sqlite3.connect(self.db)
        self.c.execute('''CREATE TABLE cost_supplements(created_at TEXT,
            token_input_other INTEGER,token_input_cached INTEGER,token_output INTEGER,
            injection_total INTEGER,cost_amount REAL,currency_symbol TEXT)''')
    def tearDown(self):
        self.c.close();self.temp.cleanup()
    def row(self, cost, cur='CNY', ts='2099-01-01 00:00:00'):
        self.c.execute('INSERT INTO cost_supplements VALUES(?,10,90,5,50,?,?)',(ts,cost,cur));self.c.commit()
    def read(self):return usage.ledger('2000-01-01',db=self.db,target='CNY',rates={'CNY':7})
    def test_cny_never_multiplied(self):
        self.row(.5);self.assertEqual(self.read().cost,.5)
    def test_mixed_currency_conversion(self):
        self.row(.5);self.row(1,'USD');self.assertEqual(self.read().cost,7.5)
    def test_unknown_currency_or_missing_cost(self):
        for cur,cost in [('',1),('XYZ',1),('CNY',None)]:
            self.c.execute('DELETE FROM cost_supplements');self.row(cost,cur)
            self.assertFalse(self.read().cost_known)
    def test_injection_not_double_counted(self):
        self.row(.5);self.assertEqual(self.read().tokens,105)
    def test_beijing_daily_boundary(self):
        now=datetime(2026,10,29,1,tzinfo=usage.TZ)
        self.assertEqual(usage.window_start(now=now),'2026-10-28 16:00:00')
        self.assertEqual(usage.window_start(now=now,monthly=True),'2026-09-30 16:00:00')
    def test_boundary_excludes_previous_day(self):
        self.row(1,ts='2026-10-28 15:59:59');self.row(2,ts='2026-10-28 16:00:00')
        self.assertEqual(usage.ledger('2026-10-28 16:00:00',db=self.db).cost,2)
    def test_global_monthly_guard(self):
        self.row(1)
        self.cfg.write_text(json.dumps({'budgets_cost':{'global_daily':10,'global_monthly':.5}}))
        allowed,why=usage.budget_status(config=self.cfg,db=self.db)
        self.assertFalse(allowed);self.assertIn('monthly',why)
    def test_unknown_ledger_skips_proactive(self):
        allowed,_=usage.budget_status(config=Path(self.temp.name)/'missing',db=self.db)
        self.assertFalse(allowed)
    def test_no_limits_need_no_ledger(self):
        self.cfg.write_text('{}')
        self.assertTrue(usage.budget_status(config=self.cfg,db='missing')[0])


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def context(self,result=None):
        api=types.SimpleNamespace(post_group_message=AsyncMock(return_value=result),post_c2c_message=AsyncMock(return_value=result))
        platform=types.SimpleNamespace(meta=lambda:types.SimpleNamespace(name='qq_official'),client=types.SimpleNamespace(api=api),_session_last_message_id={})
        return types.SimpleNamespace(get_platform_inst=lambda _:platform),api,platform
    async def test_receipt_is_required(self):
        for result in [None,{}, {'ok':True}]:
            ctx,api,_=self.context(result)
            with self.assertRaises(RuntimeError):await send_text(ctx,'qq:GroupMessage:g','hello')
            self.assertEqual(api.post_group_message.await_count,1)
    async def test_real_receipt(self):
        ctx,api,_=self.context({'id':'receipt'})
        self.assertEqual(await send_text(ctx,'qq:GroupMessage:g','hello'),'receipt')
    async def test_optional_passive_context(self):
        ctx,api,p=self.context({'id':'ok'});p._session_last_message_id['g']='msg'
        await send_text(ctx,'qq:GroupMessage:g','hello',reply_msg_id='msg')
        self.assertEqual(api.post_group_message.call_args.kwargs['msg_id'],'msg')
    async def test_missing_platform(self):
        ctx=types.SimpleNamespace(get_platform_inst=lambda _:None)
        with self.assertRaises(RuntimeError):await send_text(ctx,'qq:GroupMessage:g','hello')
    async def test_timeout_does_not_resend(self):
        ctx,api,_=self.context();api.post_group_message.side_effect=asyncio.TimeoutError
        with self.assertRaises(asyncio.TimeoutError):await send_text(ctx,'qq:GroupMessage:g','hello')
        self.assertEqual(api.post_group_message.await_count,1)
    async def test_channel_rejected(self):
        ctx,api,_=self.context({'id':'ok'})
        with self.assertRaises(ValueError):await send_text(ctx,'qq:Other:g','hello')
        api.post_group_message.assert_not_called()


class ContextTests(unittest.IsolatedAsyncioTestCase):
    def event(self):
        extra={}
        return types.SimpleNamespace(get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.__setitem__(k,v))
    async def test_concurrent_read_merged(self):
        event=self.event();fn=AsyncMock(return_value='same')
        result=await asyncio.gather(read_once(event,'key',fn),read_once(event,'key',fn))
        self.assertEqual(result,['same','same']);fn.assert_awaited_once()
    async def test_event_scope_isolated(self):
        fn=AsyncMock(return_value='same')
        await read_once(self.event(),'key',fn);await read_once(self.event(),'key',fn)
        self.assertEqual(fn.await_count,2)
    async def test_failed_read_can_retry(self):
        event=self.event();fn=AsyncMock(side_effect=[ValueError('error'),'ok'])
        with self.assertRaises(ValueError):await read_once(event,'key',fn)
        self.assertEqual(await read_once(event,'key',fn),'ok')


class MemoryTests(unittest.TestCase):
    def formatter(self):
        path=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_livingmemory/core/utils/formatting.py'
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        constants=types.ModuleType('_fm.core.base.constants')
        constants.MEMORY_INJECTION_HEADER='<memory>'
        constants.MEMORY_INJECTION_FOOTER='</memory>'
        namespace={'__package__':'_fm.core.utils','Any':object,'time':time,'datetime':datetime,
                   'json':json,'logger':logging.getLogger('test'),'_MAX_INJECTION_ENTRY_CHARS':800}
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef)],type_ignores=[]),str(path),'exec'),namespace)
        sys.modules['_fm.core.base.constants']=constants
        return namespace['format_memories_for_injection']
    def test_formatter_total_budget_and_wrappers(self):
        fn=self.formatter()
        rows=[{'content':str(i)+'长'*1000,'score':i/10} for i in range(8)]
        result=fn(rows)
        self.assertLessEqual(len(result),2400)
        self.assertTrue(result.startswith('<memory>'));self.assertTrue(result.endswith('</memory>'))
        self.assertIn('7长',result)
    def test_small_formatter_budget_omits_instead_of_overflowing(self):
        fn=self.formatter()
        self.assertEqual(fn([{'content':'a'}],max_chars=10),'')
    def test_rank_deduplicate_and_filter_without_mutation(self):
        rows=[{'content':'same text','score':.1}, {'content':'same text','score':.9},
              {'content':'bad','score':1,'metadata':{'summary_quality':'low'}},
              {'content':'other','score':.5}]
        before=json.dumps(rows)
        selected=memory.select_memories(rows)
        self.assertEqual([x['content'] for x in selected],['same text','other'])
        self.assertEqual(selected[0]['score'],.9);self.assertEqual(json.dumps(rows),before)
    def test_json_metadata_supported(self):
        self.assertEqual(memory.select_memories([{'content':'low','metadata':'{"summary_quality":"low"}'}]),[])


class AuditTests(unittest.TestCase):
    def test_event_audit_never_persists_chat_or_error_body(self):
        path=ROOT/'scripts/diagnostics/bot_event_audit.py'
        spec=importlib.util.spec_from_file_location('bot_audit',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        line='2026-10-29T08:00:00.000Z [INFO] user said secret message'
        self.assertIsNone(mod.parse_event(line))
        result=mod.parse_event('2026-10-29T08:00:00.000Z [WARN] [core.module:12]: secret message')
        self.assertEqual(result['source'],'core.module:12')
        self.assertNotIn('secret',json.dumps(result))


class PluginRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_records_failure_and_recovery(self):
        path=ROOT/'plugins/astrbot_plugin_wushu_ops/main.py'
        class Failure(RuntimeError):
            def user_message(self):return 'down'
        monitor=function(path,'_monitor_once',{'STATUS_SERVERS':['main'],'LOCAL_PORTS':[],
            'FAST_RETRY':None,'NetError':Failure})
        health=AsyncMock(side_effect=[{'app':'1','uptimeSec':100},Failure('down'),Failure('down'),{'app':'1','uptimeSec':200}])
        obj=types.SimpleNamespace(config={},net=types.SimpleNamespace(api=types.SimpleNamespace(health=health)),
                                  _fails={},_state={},_broadcast=AsyncMock())
        for _ in range(4):await monitor(obj)
        messages=[call.args[0] for call in obj._broadcast.await_args_list]
        self.assertEqual(len(messages),2)
        self.assertIn('无响应',messages[0]);self.assertIn('恢复响应',messages[1])
    async def test_rank_retry_only_failed_targets_and_is_bounded(self):
        path=ROOT/'plugins/astrbot_plugin_wushu_ops/assistant_runtime.py'
        clock=types.SimpleNamespace(now=lambda tz:datetime(2026,10,29,8,0))
        now=[1000.0]
        run=function(path,'_rankings',{'datetime':clock,'TZ':None,'time':types.SimpleNamespace(time=lambda:now[0]),
            '_log':lambda:logging.getLogger('test'),'random':types.SimpleNamespace(choice=lambda x:x[0]),'clean':str})
        targets=['大肥鱼-官方:GroupMessage:a','大肥鱼-官方:GroupMessage:b'];state={}
        async def send(target,text):
            if target.endswith(':b'):raise RuntimeError('no receipt')
            return 'id'
        api=types.SimpleNamespace(daily_challenges=AsyncMock(return_value={'rankingScope':'cumulative','rows':[{'rank':1,'name':'A','points':1}]}))
        obj=types.SimpleNamespace(plugin=types.SimpleNamespace(_targets=lambda:targets,net=types.SimpleNamespace(api=api)),
             get=lambda k,d=None:state.get(k,d),put=lambda k,v:state.__setitem__(k,v),send=AsyncMock(side_effect=send))
        for _ in range(3):
            with self.assertRaises(RuntimeError):await run(obj)
            now[0]+=301
        await run(obj)  # Three attempts exhausted; no fourth request.
        self.assertEqual(obj.send.await_count,4)  # a once, b three times
        self.assertEqual(api.daily_challenges.await_count,6)
    async def test_cover_wait_does_not_consume_another_users_audio(self):
        path=ROOT/'plugins/astrbot_plugin_dafeiyu_cover/main.py'
        handle=function(path,'on_message',{'time':time,'re':__import__('re')})
        obj=types.SimpleNamespace(enabled=True,_pending={},_waiting={('group','owner'):time.time()+60},_wait_mode={},_find_audio=Mock())
        event=types.SimpleNamespace(unified_msg_origin='group',get_sender_id=lambda:'other')
        self.assertEqual([r async for r in handle(obj,event)],[])
        obj._find_audio.assert_not_called()
    async def test_birthday_stop_awaits_cancellation(self):
        path=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py'
        stop=function(path,'stop',{'asyncio':asyncio})
        obj=types.SimpleNamespace(_task=asyncio.create_task(asyncio.sleep(60)))
        task=obj._task;await stop(obj)
        self.assertTrue(task.cancelled());self.assertIsNone(obj._task)
    async def test_unauthorized_group_send_never_calls_api(self):
        path=ROOT/'plugins/astrbot_plugin_wushu_ops/main.py'
        send=function(path,'send_group_message',{})
        obj=types.SimpleNamespace(_is_ops_admin=lambda e:False)
        self.assertIn('仅管理员',await send(obj,object(),'message'))
    async def test_broadcast_failure_never_uses_second_send_path(self):
        path=ROOT/'plugins/astrbot_plugin_wushu_ops/main.py'
        api=types.ModuleType('astrbot.api');api.logger=logging.getLogger('test')
        broadcast=function(path,'_broadcast',{})
        obj=types.SimpleNamespace(assistant=types.SimpleNamespace(send=AsyncMock(side_effect=TimeoutError())),_targets=lambda:['qq:GroupMessage:g'])
        with patch.dict(sys.modules,{'astrbot.api':api}):
            sent,failed=await broadcast(obj,'message')
        self.assertEqual(sent,[]);self.assertEqual(len(failed),1);obj.assistant.send.assert_awaited_once()
    async def test_birthday_countdown_uses_calendar(self):
        path=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py'
        fn=function(path,'upcoming',{'date':date,'_md':lambda x:tuple(map(int,x.split('-')))})
        rows=[{'birthday':'11-1'},{'birthday':'2-29'}]
        result=fn(rows,time.strptime('2026-10-29','%Y-%m-%d'))
        self.assertEqual(result[0][0],3)
        self.assertEqual(result[1][0],(date(2028,2,29)-date(2026,10,29)).days)

    async def test_birthday_invalid_dates_and_year_boundary_are_bounded(self):
        path=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py'
        fn=function(path,'upcoming',{'date':date,'_md':lambda x:tuple(map(int,x.split('-')))})
        rows=[{'birthday':v} for v in ['2-30','4-31','0-1','13-1','2-29']]
        result=fn(rows,time.strptime('2026-10-29','%Y-%m-%d'))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0][1]['birthday'],'2-29')
        self.assertEqual(fn([{'birthday':'2-29'}],time.strptime('9999-12-31','%Y-%m-%d')),[])
