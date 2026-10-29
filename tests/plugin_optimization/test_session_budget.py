import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from test_runtime import function, ROOT
from dafeiyu_runtime import session_budget as budget, usage


class SessionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.policy=self.root/'policy.json';self.db=self.root/'cost.db'
        self.cfg={'enabled':True,'private_exempt':True,'group_daily_budget_cny':.5,'effective_from':'2026-10-29T20:00:00+08:00'}
        self.policy.write_text(json.dumps(self.cfg))
        con=sqlite3.connect(self.db)
        con.execute('CREATE TABLE cost_supplements(umo TEXT,created_at TEXT,cost_amount REAL,currency_symbol TEXT)')
        con.executemany('INSERT INTO cost_supplements VALUES(?,?,?,?)',[
            ('bot:GroupMessage:a','2026-10-29 12:01:00',.3,'CNY'),
            ('bot:GroupMessage:a','2026-10-29T12:02:00',.2,'CNY'),
            ('bot:GroupMessage:a','2026-10-29 11:59:00',99,'CNY'),
            ('bot:GroupMessage:b','2026-10-29 12:01:00',.1,'CNY'),
            ('bot:FriendMessage:p','2026-10-29 12:01:00',999,'CNY')])
        con.commit();con.close()
        self.now=datetime(2026,10,29,21,tzinfo=budget.TZ)

    def tearDown(self):self.temp.cleanup()

    def status(self,origin):return budget.status(origin,policy=self.policy,db=self.db,now=self.now)

    def test_private_unlimited_even_with_unavailable_cost_database(self):
        self.assertEqual(budget.status('bot:FriendMessage:p',policy=self.policy,db='missing'),(True,'私聊不限额'))

    def test_group_cap_counts_both_timestamp_formats_and_keeps_groups_separate(self):
        self.assertFalse(self.status('bot:GroupMessage:a')[0])
        self.assertTrue(self.status('bot:GroupMessage:b')[0])
        self.assertAlmostEqual(budget.group_spend('bot:GroupMessage:a',self.cfg,db=self.db,now=self.now),.5)

    def test_unknown_context_is_not_private(self):self.assertFalse(self.status('')[0])

    def test_missing_policy_keeps_legacy_behavior(self):
        self.assertIsNone(budget.status(policy=self.root/'absent'))

    def test_unreadable_group_cost_does_not_grant_unlimited(self):
        self.assertFalse(budget.status('bot:GroupMessage:a',policy=self.policy,db='missing')[0])

    def test_next_day_resets_and_does_not_reapply_initial_start(self):
        next_day=datetime(2026,10,30,0,tzinfo=budget.TZ)
        self.assertTrue(budget.status('bot:GroupMessage:a',policy=self.policy,db=self.db,now=next_day)[0])

    def test_shared_gate_uses_policy_before_old_global_guard(self):
        with patch.object(budget,'status',return_value=(True,'private')):
            self.assertEqual(usage.budget_status(origin='bot:FriendMessage:p'),(True,'private'))


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_private_and_group_contexts_are_isolated(self):
        async def task(origin):
            with budget.scope(origin):
                await asyncio.sleep(0)
                return await asyncio.to_thread(budget.ORIGIN.get)
        before=budget.ORIGIN.get()
        values=await asyncio.gather(task('bot:FriendMessage:p'),task('bot:GroupMessage:a'))
        self.assertEqual(values,['bot:FriendMessage:p','bot:GroupMessage:a'])
        self.assertEqual(budget.ORIGIN.get(),before)

    async def test_message_handler_sets_scope_before_direct_commands(self):
        handler=function(ROOT/'plugins/astrbot_plugin_request_ledger/main.py','remember_budget_origin',{})
        from types import SimpleNamespace
        with budget.scope(''):
            await handler(None,SimpleNamespace(unified_msg_origin='bot:FriendMessage:p'))
            self.assertEqual(budget.ORIGIN.get(),'bot:FriendMessage:p')
