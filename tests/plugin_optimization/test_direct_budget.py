import asyncio
import sys
import types
import logging
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from dafeiyu_runtime.paid import guarded_model_call, ModelBudgetBlocked
from test_voice_delivery import extract, PATCHES


class DirectBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowed_call_preserves_arguments_and_result(self):
        call=AsyncMock(return_value='result')
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            self.assertEqual(await guarded_model_call(call,prompt='synthetic',max_tokens=20),'result')
        call.assert_awaited_once_with(prompt='synthetic',max_tokens=20)

    async def test_blocked_or_unknown_does_not_call_provider(self):
        for reason in ['daily','monthly','unreadable']:
            call=AsyncMock()
            with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,reason)):
                with self.assertRaises(ModelBudgetBlocked):await guarded_model_call(call,prompt='synthetic')
            call.assert_not_called()

    async def test_budget_rechecked_before_retry(self):
        call=AsyncMock(return_value='first')
        with patch('dafeiyu_runtime.paid.budget_status',side_effect=[(True,'ok'),(False,'spent')]):
            await guarded_model_call(call,prompt='first')
            with self.assertRaises(ModelBudgetBlocked):await guarded_model_call(call,prompt='retry')
        self.assertEqual(call.await_count,1)

    async def test_unexpected_budget_error_prevents_provider_call(self):
        call=AsyncMock()
        with patch('dafeiyu_runtime.paid.budget_status',side_effect=RuntimeError('database failed')):
            with self.assertRaises(RuntimeError):await guarded_model_call(call,prompt='synthetic')
        call.assert_not_called()

    async def test_cancellation_propagates(self):
        call=AsyncMock(side_effect=asyncio.CancelledError())
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(True,'ok')):
            with self.assertRaises(asyncio.CancelledError):await guarded_model_call(call,prompt='synthetic')

    async def test_cover_notification_skips_model_when_budget_blocked(self):
        method=extract(PATCHES/'astrbot_plugin_matsuko_cover/main.py','_notify_llm_with_context',
                       {'guarded_model_call':guarded_model_call,'ModelBudgetBlocked':ModelBudgetBlocked,'logger':logging.getLogger('test')})
        provider=types.SimpleNamespace(text_chat=AsyncMock())
        plugin=types.SimpleNamespace(enable_llm_success_notify=True,
            context=types.SimpleNamespace(get_using_provider=lambda origin:provider))
        event=types.SimpleNamespace(unified_msg_origin='isolated-test',send=AsyncMock())
        with patch('dafeiyu_runtime.paid.budget_status',return_value=(False,'daily')):
            self.assertIsNone(await method(plugin,event,'synthetic','success'))
        provider.text_chat.assert_not_called();event.send.assert_not_called()
