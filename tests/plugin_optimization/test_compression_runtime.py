"""Run on the server's actual framework; provider is a no-network fixture."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
try:
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.message import Message
except ImportError:
    LLMSummaryCompressor=None


@unittest.skipIf(LLMSummaryCompressor is None,'Requires actual AstrBot framework')
class CompressionTests(unittest.IsolatedAsyncioTestCase):
    def history(self):
        messages=[Message(role='system',content='Keep the current identity and rules.')]
        for i in range(10):
            messages.extend([Message(role='user',content=f'Question {i}: '+'context '*400),Message(role='assistant',content=f'Answer {i}: '+'facts '*400)])
        messages.append(Message(role='user',content='ACTIVE REQUEST: keep this text exact.'))
        return messages
    def compressor(self,text='fixture summary'):
        provider=SimpleNamespace(provider_config={'modalities':['text']},text_chat=AsyncMock(return_value=SimpleNamespace(completion_text=text)))
        return LLMSummaryCompressor(provider,keep_recent_ratio=.25),provider
    async def test_system_and_latest_request_preserved(self):
        compressor,provider=self.compressor();messages=self.history();result=await compressor(messages)
        self.assertIs(result[0],messages[0]);self.assertIs(result[-1],messages[-1]);self.assertLess(len(result),len(messages));provider.text_chat.assert_awaited_once()
    async def test_failure_preserves_history(self):
        compressor,provider=self.compressor();provider.text_chat.side_effect=RuntimeError('fixture failure')
        messages=self.history();self.assertIs(await compressor(messages),messages)
    async def test_empty_summary_preserves_history(self):
        compressor,_=self.compressor('');messages=self.history();self.assertIs(await compressor(messages),messages)
    async def test_current_request_never_summarized_at_zero_ratio(self):
        compressor,_=self.compressor();compressor.keep_recent_ratio=0;messages=self.history();result=await compressor(messages);self.assertIs(result[-1],messages[-1])
    async def test_threshold_and_disabled_window(self):
        compressor,_=self.compressor();self.assertFalse(compressor.should_compress([],100,0));self.assertFalse(compressor.should_compress([],1000,64000));self.assertTrue(compressor.should_compress([],53000,64000))
