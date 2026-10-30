import ast
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
import sys
import importlib.util

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'plugins'))
from dafeiyu_runtime.session_budget import scope,ORIGIN


class BirthdayGroundingTests(unittest.TestCase):
    def test_music_preference_does_not_prove_playing_skill(self):
        path=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_style.py'
        spec=importlib.util.spec_from_file_location('birthday_grounding',path);style=importlib.util.module_from_spec(spec);spec.loader.exec_module(style)
        text='林舟，生日快乐。弹得一手好琴，'+('愿你新的一岁开心平安。'*12)
        self.assertFalse(style.acceptable(text,'林舟','喜欢听钢琴曲'))
        self.assertTrue(style.acceptable(text,'林舟','会弹琴，经常演奏'))


class MemorySourceTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self,origins):
        path=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_livingmemory/core/processors/memory_processor.py'
        node=next(n for n in ast.walk(ast.parse(path.read_text(encoding='utf-8'))) if isinstance(n,ast.AsyncFunctionDef) and n.name=='process_conversation')
        ns={'Message':object,'Any':object,'datetime':datetime,'logger':SimpleNamespace(info=lambda *a:None,debug=lambda *a:None,error=lambda *a,**k:None)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_memory_processor','exec'),ns)
        seen=[]
        async def call(**kw):seen.append(ORIGIN.get());raise RuntimeError('stop before network')
        obj=SimpleNamespace(_format_conversation=lambda _: 'synthetic',_get_chat_prompt=lambda _:'{conversation}',_build_system_prompt_with_persona=AsyncMock(return_value='synthetic'),_call_llm_with_retry=call)
        with scope('qq:GroupMessage:caller'):
            with self.assertRaises((ValueError,RuntimeError)):
                await ns['process_conversation'](obj,[SimpleNamespace(session_id=o) for o in origins])
            self.assertEqual(ORIGIN.get(),'qq:GroupMessage:caller')
        return seen

    async def test_archive_origin_overrides_unrelated_caller(self):
        self.assertEqual(await self.run_case(['qq:GroupMessage:actual']),['qq:GroupMessage:actual'])

    async def test_mixed_sessions_never_start_synthesis(self):
        self.assertEqual(await self.run_case(['qq:GroupMessage:a','qq:GroupMessage:b']),[])
