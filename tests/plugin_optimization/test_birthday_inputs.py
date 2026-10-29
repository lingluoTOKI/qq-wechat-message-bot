import ast
import re
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
from test_runtime import function

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory'


class BirthdayInputTests(unittest.TestCase):
    def test_calendar_normalization_rejects_impossible_dates(self):
        tree=ast.parse((BASE/'birthday.py').read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,(ast.Assign,ast.FunctionDef))]
        ns={'re':re,'date':date};exec(compile(ast.Module(body=nodes,type_ignores=[]),'birthday','exec'),ns)
        for value in ['2月30日','4月31日','13月40日']:self.assertEqual(ns['normalize'](value),'')
        self.assertEqual(ns['normalize']('2月29日'),'2月29日')
        self.assertEqual(ns['detect_self']('我的生日是11月7日'),'11月7日')
        self.assertEqual(ns['detect_self']('我的生日是2月30日'),'')

    def test_actual_framework_keeps_nickname_and_spaces(self):
        try:from astrbot.core.star.filter.command import CommandFilter,GreedyStr
        except ImportError:self.skipTest('Requires actual AstrBot framework')
        tree=ast.parse((BASE/'main.py').read_text(encoding='utf-8'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='mem_cmd')
        node.decorator_list=[]
        ns={'GreedyStr':GreedyStr,'AstrMessageEvent':object}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'mem_cmd','exec'),ns)
        command=CommandFilter('记忆',handler_md=types.SimpleNamespace(handler=ns['mem_cmd']))
        self.assertIs(command.handler_params['arg'],GreedyStr)
        actual=command.validate_and_convert_params(['生日预览','后排靠窗','王の故乡'],command.handler_params)
        self.assertEqual(actual['arg'],'生日预览 后排靠窗 王の故乡')
        self.assertEqual(command.validate_and_convert_params([],command.handler_params)['arg'],'')


class BirthdayCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_receives_complete_nickname(self):
        method=function(BASE/'main.py','mem_cmd',{})
        preview=AsyncMock(return_value='synthetic preview')
        obj=types.SimpleNamespace(birthdays=types.SimpleNamespace(preview=preview))
        event=types.SimpleNamespace(stop_event=lambda:None,plain_result=lambda text:text)
        result=[r async for r in method(obj,event,'生日预览 后排靠窗 王の故乡')]
        self.assertEqual(result,['synthetic preview'])
        preview.assert_awaited_once_with('后排靠窗 王の故乡')
