import ast
import sys
import types
import unittest
import asyncio
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'plugins'))
from dafeiyu_runtime.selection import claim,owns


class SelectionTests(unittest.TestCase):
    def test_latest_intent_wins(self):
        old=claim('g','a','listen',60)
        claim('g','a','cover',60)
        self.assertFalse(owns('g','a','listen',old))
        self.assertTrue(owns('g','a','cover'))
    def test_other_user_and_group_are_isolated(self):
        lease=claim('g','a','listen',60)
        claim('g','b','cover',60);claim('h','a','cover',60)
        self.assertTrue(owns('g','a','listen',lease))
        self.assertFalse(owns('g','b','listen',lease))
    def test_old_timeout_cannot_claim_new_search(self):
        old=claim('g','a','listen',60);new=claim('g','a','listen',60)
        self.assertFalse(owns('g','a','listen',old));self.assertTrue(owns('g','a','listen',new))
    def test_music_filter_rejects_other_user_and_cover_selection(self):
        p=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_listen_music/main.py'
        tree=ast.parse(p.read_text(encoding='utf-8'));node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_SelectionSessionFilter')
        scope={'SessionFilter':object,'AstrMessageEvent':object,'_is_selection_reply':lambda t:t=='1'}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),scope)
        lease=claim('g','a','listen',60);f=scope['_SelectionSessionFilter']('g','a',lease)
        def event(sender):return types.SimpleNamespace(unified_msg_origin='g',message_str='1',get_sender_id=lambda:sender)
        self.assertEqual(f.filter(event('a')),f.waiter_id);self.assertEqual(f.filter(event('b')),'')
        claim('g','a','cover',60);self.assertEqual(f.filter(event('a')),'')

        other=claim('g','b','listen',60)
        fb=scope['_SelectionSessionFilter']('g','b',other)
        self.assertNotEqual(f.waiter_id,fb.waiter_id)
        self.assertEqual(fb.filter(event('b')),fb.waiter_id)

class HostWaiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_users_register_and_cleanup_independently(self):
        try:
            from astrbot.core.utils.session_waiter import SessionWaiter, SessionFilter, USER_SESSIONS
        except ImportError:
            self.skipTest('Requires actual AstrBot framework')
        p=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_listen_music/main.py'
        tree=ast.parse(p.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_SelectionSessionFilter')
        ns={'SessionFilter':SessionFilter,'AstrMessageEvent':object,'_is_selection_reply':lambda t:t=='1'}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),ns)
        cls=ns['_SelectionSessionFilter'];origin='isolated-acceptance-room'
        fa=cls(origin,'a',claim(origin,'a','listen',60))
        fb=cls(origin,'b',claim(origin,'b','listen',60))
        wa=SessionWaiter(fa,fa.waiter_id,False);wb=SessionWaiter(fb,fb.waiter_id,False)
        received=[]
        async def handler(controller,event):
            received.append(event.get_sender_id());controller.stop()
        tasks=[asyncio.create_task(w.register_wait(handler,timeout=5)) for w in (wa,wb)]
        try:
            await asyncio.sleep(0)
            self.assertIs(USER_SESSIONS[fa.waiter_id],wa)
            self.assertIs(USER_SESSIONS[fb.waiter_id],wb)
            def event(uid):return types.SimpleNamespace(unified_msg_origin=origin,message_str='1',get_sender_id=lambda:uid)
            await SessionWaiter.trigger(fa.filter(event('a')),event('a'))
            await tasks[0]
            self.assertIs(USER_SESSIONS[fb.waiter_id],wb)
            await SessionWaiter.trigger(fb.filter(event('b')),event('b'))
            await tasks[1]
            self.assertEqual(received,['a','b'])
        finally:
            for w in (wa,wb):w.session_controller.stop()
            await asyncio.gather(*tasks,return_exceptions=True)
