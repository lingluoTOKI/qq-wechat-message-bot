import ast
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[2]/'vendor/astrbot/astrbot_plugin_lol_esports/main.py'


class EsportsTests(unittest.IsolatedAsyncioTestCase):
    def load(self, rows):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('_parse','_fmt','ScheduleTool')]
        ns={'datetime':datetime,'timedelta':timedelta,'timezone':timezone,'CN':timezone(timedelta(hours=8)),
            'dataclass':dataclass,'field':field,'FunctionTool':object,'AstrMessageEvent':object,'logger':logging.getLogger('test')}
        async def fetch():return rows
        ns['fetch_events']=fetch
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
        return ns

    def row(self,state='completed',right=1):
        return {'startTime':datetime.now(timezone.utc).isoformat(),'state':state,'match':{'teams':[
            {'code':'AAA','result':{'gameWins':2}}, {'code':'BBB','result':{'gameWins':right}}]}}

    async def test_missing_score_not_rendered_as_none_or_unstarted(self):
        ns=self.load([self.row(right=None)])
        text=await ns['ScheduleTool']().run(None)
        self.assertIn('比分待更新',text);self.assertNotIn('None',text);self.assertNotIn('未开始',text)

    async def test_valid_score_and_unknown_state(self):
        ns=self.load([self.row(),self.row(state='postponed')])
        text=await ns['ScheduleTool']().run(None)
        self.assertIn('2-1',text);self.assertIn('状态待确认',text)

    async def test_empty_page_is_not_claimed_to_be_full_schedule(self):
        ns=self.load([]);text=await ns['ScheduleTool']().run(None)
        self.assertIn('当前页未找到',text)
        self.assertIsNone(ns['_parse'](None));self.assertIsNone(ns['_parse']('2026-10-29T12:00:00'))
