import asyncio
import re
import types
import sys
import unittest
from unittest.mock import AsyncMock
from test_voice_delivery import extract, PATCHES
sys.path.insert(0,str(PATCHES.parents[1]))


class MediaFlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self):
        path=PATCHES/'astrbot_plugin_parser/main.py'
        result=types.SimpleNamespace(get_resource_id=lambda:'video-1')
        obj=types.SimpleNamespace(parser_map={'example':types.SimpleNamespace(parse=AsyncMock(return_value=result))},
            key_pattern_list=[('example',re.compile(r'https://example/\w+'))],
            sender=types.SimpleNamespace(send_parse_result=AsyncMock(return_value=True)))
        for name in ['_parse_matched','_send_event_result','parse_and_send']:
            setattr(obj,name,types.MethodType(extract(path,name,{}),obj))
        return obj,result

    def event(self):
        extras={}
        return types.SimpleNamespace(get_extra=lambda key,default=None:extras.get(key,default),set_extra=lambda key,value:extras.__setitem__(key,value))

    async def test_automatic_then_tool_parses_and_sends_once(self):
        obj,result=self.setup_flow();event=self.event();match=obj.key_pattern_list[0][1].search('https://example/video')
        parsed=await obj._parse_matched(event,'example',match)
        self.assertTrue(await obj._send_event_result(event,parsed,match.group(0),'example'))
        self.assertTrue(await obj.parse_and_send(event,'https://example/video'))
        obj.parser_map['example'].parse.assert_awaited_once();obj.sender.send_parse_result.assert_awaited_once()

    async def test_concurrent_tool_calls_share_work(self):
        obj,_=self.setup_flow();event=self.event()
        self.assertEqual(await asyncio.gather(*(obj.parse_and_send(event,'https://example/video') for _ in range(3))),[True]*3)
        obj.parser_map['example'].parse.assert_awaited_once();obj.sender.send_parse_result.assert_awaited_once()

    async def test_different_events_do_not_share_media(self):
        obj,_=self.setup_flow()
        for _ in range(2):await obj.parse_and_send(self.event(),'https://example/video')
        self.assertEqual(obj.parser_map['example'].parse.await_count,2)
        self.assertEqual(obj.sender.send_parse_result.await_count,2)

    async def test_parse_error_can_retry_and_false_send_is_not_success(self):
        obj,result=self.setup_flow();event=self.event()
        obj.parser_map['example'].parse.side_effect=[RuntimeError('network'),result]
        with self.assertRaises(RuntimeError):await obj.parse_and_send(event,'https://example/video')
        obj.sender.send_parse_result.return_value=False
        self.assertFalse(await obj.parse_and_send(event,'https://example/video'))
        self.assertFalse(await obj.parse_and_send(event,'https://example/video'))
        obj.sender.send_parse_result.assert_awaited_once()
