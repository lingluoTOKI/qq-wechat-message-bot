import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from test_ledger import Ledger,Capture,extract_usage
from dafeiyu_runtime.session_budget import scope
from dafeiyu_runtime.zhipu_attribution import record


class ZhipuTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_capture_scope_usage_dedup_and_separate_official_summary(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=Ledger(Path(td)/'requests.db');cost=str(Path(td)/'cost.db')
            c=sqlite3.connect(cost);c.execute('CREATE TABLE cost_supplements(umo TEXT,provider_id TEXT,provider_model TEXT,response_id TEXT,token_input_other INT,token_input_cached INT,token_output INT,injection_total INT,cost_amount REAL,currency_symbol TEXT,created_at TEXT)');c.close()
            def attributed(*a,**k):return record(*a,**k,path=cost)
            def respond(req):return httpx.Response(200,json={'id':'glm-r','model':'glm-5.3-flashx','usage':{'prompt_tokens':100,'completion_tokens':20}})
            capture=Capture(ledger);capture.install()
            try:
                with patch('dafeiyu_runtime.zhipu_attribution.record',side_effect=attributed),scope('qq:GroupMessage:one'):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                        for _ in range(2):await client.post('https://open.bigmodel.cn/api/paas/v4/chat/completions',json={'model':'glm-5.3-flashx'})
                self.assertEqual(capture.errors,0)
                self.assertEqual(ledger.summary(0)['requests'],0)
                c=sqlite3.connect(cost);self.assertEqual(c.execute('SELECT count(*),sum(cost_amount),max(umo) FROM cost_supplements').fetchone(),(1,.00034,'qq:GroupMessage:one'));c.close()
            finally:capture.uninstall()

    async def test_unknown_usage_never_becomes_zero_cost(self):
        with tempfile.TemporaryDirectory() as td:
            cost=str(Path(td)/'cost.db');c=sqlite3.connect(cost);c.execute('CREATE TABLE cost_supplements(umo TEXT,provider_id TEXT,provider_model TEXT,response_id TEXT,token_input_other INT,token_input_cached INT,token_output INT,injection_total INT,cost_amount REAL,currency_symbol TEXT,created_at TEXT)');c.close()
            record('unknown','qq:GroupMessage:g','glm-5.3-flashx',1,None,path=cost)
            c=sqlite3.connect(cost);self.assertIsNone(c.execute('SELECT cost_amount FROM cost_supplements').fetchone()[0]);c.close()
