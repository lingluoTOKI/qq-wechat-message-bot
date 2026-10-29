import ast
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch
from test_voice_delivery import extract, PATCHES
sys.path.insert(0,str(PATCHES.parents[1]))
from dafeiyu_runtime.paid import guarded_model_call,ModelBudgetBlocked


class MemeBudgetTests(unittest.IsolatedAsyncioTestCase):
    def caption(self,name):
        p=PATCHES/'meme_manager/backend/semantic/caption.py'
        node=next(n for n in ast.parse(p.read_text(encoding='utf-8')).body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        ns={'guarded_model_call':guarded_model_call,'CAPTION_TOOL_PROMPT_SUFFIX':'tool',
            'CAPTION_SYSTEM_PROMPT':'system','_build_caption_tool_set':lambda names:[],
            '_structured_output_is_unsupported':lambda error:isinstance(error,TypeError)}
        module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
        ast.fix_missing_locations(module);exec(compile(module,str(p),'exec'),ns)
        return ns[name]

    async def test_caption_modes_do_not_call_provider_when_blocked(self):
        context=types.SimpleNamespace(llm_generate=AsyncMock())
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,'daily')):
            with self.assertRaises(ModelBudgetBlocked):
                await self.caption('_request_caption_tool_response')(context,'vision','synthetic',[],[])
            with self.assertRaises(ModelBudgetBlocked):
                await self.caption('_request_caption_json_response')(context,'vision','synthetic',[])
        context.llm_generate.assert_not_called()

    async def test_json_compatibility_retry_rechecks_budget(self):
        context=types.SimpleNamespace(llm_generate=AsyncMock(side_effect=TypeError('unsupported')))
        with patch('dafeiyu_runtime.paid.budget_status',side_effect=[(True,'ok'),(False,'spent')]):
            with self.assertRaises(ModelBudgetBlocked):
                await self.caption('_request_caption_json_response')(context,'vision','synthetic',[])
        self.assertEqual(context.llm_generate.await_count,1)

    async def test_emotion_skip_preserves_existing_reply(self):
        p=PATCHES/'meme_manager/mixins/event_handlers.py'
        method=extract(p,'_emotion_llm_generate',{'guarded_model_call':guarded_model_call,'ModelBudgetBlocked':ModelBudgetBlocked})
        event=types.SimpleNamespace(reply='original reply')
        obj=types.SimpleNamespace(_resolve_emotion_llm_provider_id=AsyncMock(return_value='vision'),
            _resolve_emotion_llm_model=lambda event:None,context=types.SimpleNamespace(llm_generate=AsyncMock()))
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,'daily')):
            self.assertIsNone(await method(obj,event,'synthetic'))
        self.assertEqual(event.reply,'original reply');obj.context.llm_generate.assert_not_called()

    async def test_allowed_caption_keeps_provider_and_prompt(self):
        context=types.SimpleNamespace(llm_generate=AsyncMock(return_value='caption'))
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            self.assertEqual(await self.caption('_request_caption_json_response')(context,'vision','synthetic',[]),'caption')
        self.assertEqual(context.llm_generate.call_args.kwargs['chat_provider_id'],'vision')
        self.assertEqual(context.llm_generate.call_args.kwargs['prompt'],'synthetic')
