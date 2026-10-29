"""Personalized birthday output, explicit relationship state and budget fallback."""
import asyncio
import importlib.util
import json
import logging
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from test_runtime import function

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory'
if not BASE.exists():BASE=ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory'


def load(name):
    spec=importlib.util.spec_from_file_location('birthday_test_'+name,BASE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


style=load('birthday_style');affinity=load('affinity');Store=load('store').Store


class StyleTests(unittest.TestCase):
    def test_long_grounded_fallback_and_profile(self):
        person={'name':'测试玩家','summary':'MC玩家，喜欢唱歌'}
        text=style.fallback(person,['生日是11月7日','手机号123','喜欢《卫戍协议》'])
        self.assertIn('方块世界',text)
        self.assertIn('卫戍协议',text)
        self.assertIn('\n\n',text)
        self.assertTrue(style.acceptable(text,person['name']))
        self.assertNotIn('11月7日',text);self.assertNotIn('123',text)
        self.assertNotIn('熬夜',text)

    def test_unknown_and_high_affinity_have_distinct_tone_without_score(self):
        person={'name':'测试人物','summary':''}
        ordinary=style.fallback(person,[])
        close=style.fallback(person,[],90)
        self.assertNotEqual(ordinary,close)
        self.assertNotIn('我很珍惜',ordinary);self.assertIn('我很珍惜',close)
        self.assertNotIn('90',close);self.assertNotIn('好感',close)

    def test_clean_preserves_paragraphs_and_rejects_leaked_score(self):
        self.assertEqual(style.clean('小洛：第一段\n\n第二段'),'第一段\n\n第二段')
        self.assertFalse(style.acceptable('测试人物生日快乐，90/100'+('祝福'*100),'测试人物'))
        self.assertEqual(style.safe_profile('爱唱歌，手机号123，收入很多。喜欢MC'),'爱唱歌，喜欢MC')


class AffinityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(str(Path(self.temp.name)/'memory.db'))
        self.store.db.executemany('INSERT INTO people(sender_id,name,aliases) VALUES(?,?,?)',
                                 [('a','后排 靠窗','["甲"]'),('b','同名','[]'),('c','同名','[]')])
        self.store.db.commit()

    def tearDown(self):self.store.close();self.temp.cleanup()

    def test_unauthorized_and_fuzzy_attempts_do_not_write(self):
        self.assertIn('仅限',affinity.command(self.store,'设置 甲 99',False))
        self.assertIn('精确',affinity.command(self.store,'设置 后排 99',True))
        self.assertIsNone(self.store.get_affinity('a'))

    def test_exact_spaced_name_and_persistence(self):
        self.assertIn('88/100',affinity.command(self.store,'设置 后排 靠窗 88',True))
        self.assertEqual(self.store.get_affinity('a'),88)
        self.store.close();self.store=Store(str(Path(self.temp.name)/'memory.db'))
        self.assertEqual(self.store.get_affinity('a'),88)
        self.assertIsNone(self.store.get_affinity('b'))

    def test_duplicate_name_and_invalid_score_not_written(self):
        self.assertIn('多个人',affinity.command(self.store,'设置 同名 80',True))
        self.assertIn('范围',affinity.command(self.store,'设置 甲 101',True))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM birthday_affinity').fetchone()[0],0)
        with self.assertRaises(ValueError):self.store.set_affinity('a',True)


class ComposeTests(unittest.IsolatedAsyncioTestCase):
    async def check_compose(self,allowed,response=None,side_effect=None):
        ask=AsyncMock(return_value=response,side_effect=side_effect)
        person={'sender_id':'synthetic','name':'测试人物','summary':'MC玩家，手机号123'}
        store=types.SimpleNamespace(get_facts=lambda *a,**k:[{'fact':'喜欢唱歌','confidence':.9},{'fact':'生日11月7日','confidence':1},{'fact':'讨厌唱歌','confidence':.1}],get_affinity=lambda _:80)
        obj=types.SimpleNamespace(plugin=types.SimpleNamespace(store=store,context=object()),config={},system_prompt=lambda:style.TASK)
        method=function(BASE/'birthday_watch.py','compose',dict(asyncio=asyncio,json=json,birthday_style=style,
            _life_facts=lambda xs:[x for x in xs if not style.NOISE.search(x)],_fallback=style.fallback,
            _clean=style.clean,llm_client=types.SimpleNamespace(ask=ask),logger=logging.getLogger('test')))
        with patch('dafeiyu_runtime.usage.budget_status',return_value=(allowed,'synthetic')):
            result=await method(obj,person)
        return result,ask

    async def test_budget_block_uses_profile_without_call(self):
        result,ask=await self.check_compose(False)
        ask.assert_not_called();self.assertIn('方块世界',result)

    async def test_model_receives_filtered_profile_and_relation(self):
        expected=style.fallback({'name':'测试人物'},[],80)
        result,ask=await self.check_compose(True,expected)
        self.assertEqual(result,expected)
        prompt=ask.call_args.args[3]
        self.assertIn('MC玩家',prompt);self.assertIn('亲近记录',prompt)
        for forbidden in ['手机号','生日11月7日','讨厌唱歌','80']:self.assertNotIn(forbidden,prompt)

    async def test_invalid_output_and_failure_fall_back_without_generation_loop(self):
        for kwargs in [dict(response='生日快乐'),dict(side_effect=RuntimeError('synthetic'))]:
            result,ask=await self.check_compose(True,**kwargs)
            self.assertTrue(style.acceptable(result,'测试人物'));self.assertEqual(ask.await_count,1)
