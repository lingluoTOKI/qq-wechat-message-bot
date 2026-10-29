import json
import logging
import types
import unittest
from unittest.mock import AsyncMock
from test_runtime import function,ROOT
from dafeiyu_runtime.context import claim_feature


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def event(self):
        extra={};data={'meta':{'detail_1':{'title':'哔哩哔哩','qqdocurl':'https://www.bilibili.com/video/test','desc':'fixture'},'news':{'tag':'哔哩哔哩','jumpUrl':'https://www.bilibili.com/video/test','title':'fixture'}}}
        return types.SimpleNamespace(get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.__setitem__(k,v),send=AsyncMock(),message_obj=types.SimpleNamespace(message=[types.SimpleNamespace(type='Json',data=data)]))
    async def test_two_plugins_cannot_claim_one_event(self):
        event=self.event();self.assertTrue(claim_feature(event,'media_parse','parser'));self.assertFalse(claim_feature(event,'media_parse','bilibili'))
        self.assertTrue(claim_feature(self.event(),'media_parse','bilibili'))
    async def test_card_with_two_metadata_shapes_sends_once(self):
        path=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_bilibili/main.py'
        fn=function(path,'parse_miniapp',{'json':json,'logger':logging.getLogger('test'),'MessageChain':lambda:types.SimpleNamespace(message=lambda t:t)})
        event=self.event();await fn(types.SimpleNamespace(enable_parse_miniapp=True),event);event.send.assert_awaited_once()
    async def test_primary_parser_claim_prevents_legacy_duplicate(self):
        path=ROOT/'plugins/patches/plugin_optimization_20261029/astrbot_plugin_bilibili/main.py'
        fn=function(path,'parse_miniapp',{'json':json,'logger':logging.getLogger('test'),'MessageChain':lambda:types.SimpleNamespace(message=lambda t:t)})
        event=self.event();claim_feature(event,'media_parse','parser');await fn(types.SimpleNamespace(enable_parse_miniapp=True),event);event.send.assert_not_awaited()
    async def test_quoted_chat_has_source_label(self):
        fn=function(ROOT/'plugins/astrbot_plugin_dafeiyu_daily_memory/recall.py','fmt_events',{'MAX_CHARS':2600,'_fmt_ts':lambda t:'fixed'})
        row={'ts':1,'role':'user','is_self':0,'sender_name':'fixture','content':'quoted','session_id':'q:FriendMessage:p','group_id':None}
        self.assertIn('[私聊]',fn([row]));row['group_id']='group123';self.assertIn('[群…oup123]',fn([row]))
