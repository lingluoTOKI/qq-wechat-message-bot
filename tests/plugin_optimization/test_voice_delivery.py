import ast
import asyncio
import base64
import logging
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parents[2]
PATCHES=ROOT/'plugins/patches/plugin_optimization_20261029'


def extract(path, name, ns):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    node.decorator_list=[]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module,str(path),'exec'),ns)
    return ns[name]


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def synth(self):
        p=PATCHES/'astrbot_plugin_dafeiyu_voice/main.py'
        return extract(p,'_synthesize',{'asyncio':asyncio})

    def config(self):
        return types.SimpleNamespace(tier_order='local,mimo,edge',local_enabled=True,mimo_enabled=False,edge_enabled=True,
            local_timeout=1,mimo_timeout=1,_tts_local=AsyncMock(return_value=(None,'offline')),
            _tts_mimo=AsyncMock(),_tts_edge=AsyncMock(return_value=(b'audio',None)))

    async def test_local_failure_uses_edge_without_paid_tier(self):
        cfg=self.config();result=await self.synth()(cfg,'synthetic')
        self.assertEqual(result,(b'audio','edge',None));cfg._tts_mimo.assert_not_called()

    async def test_unexpected_tier_error_still_falls_back(self):
        cfg=self.config();cfg._tts_local.side_effect=ValueError('bad response')
        self.assertEqual((await self.synth()(cfg,'synthetic'))[1],'edge')

    async def test_all_fail_returns_no_audio(self):
        cfg=self.config();cfg._tts_edge.return_value=(None,'offline')
        audio,tier,error=await self.synth()(cfg,'synthetic')
        self.assertIsNone(audio);self.assertIsNone(tier);self.assertTrue(error)

    def test_temp_audio_removed_on_success_and_failure(self):
        paths=[];original=tempfile.TemporaryDirectory
        def directory(*args,**kwargs):
            obj=original(*args,**kwargs);paths.append(obj.name);return obj
        convert=lambda data:data
        comp=types.SimpleNamespace(Record=types.SimpleNamespace(fromBase64=convert))
        method=extract(PATCHES/'astrbot_plugin_dafeiyu_voice/main.py','_to_record',{'tempfile':tempfile,'os':os,'base64':base64,'Comp':comp})
        with patch.object(tempfile,'TemporaryDirectory',directory):
            self.assertTrue(method(types.SimpleNamespace(silk_friendly=False),b'RIFFaudio'))
            def fail(data):raise ValueError('conversion failed')
            comp.Record.fromBase64=fail
            with self.assertRaises(ValueError):method(types.SimpleNamespace(silk_friendly=False),b'RIFFaudio')
        self.assertTrue(paths);self.assertTrue(all(not os.path.exists(p) for p in paths))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_reports_success_empty_and_send_failure(self):
        p=PATCHES/'astrbot_plugin_parser/core/sender.py'
        method=extract(p,'send_parse_result',{'logger':logging.getLogger('test')})
        event=types.SimpleNamespace(send=AsyncMock(),chain_result=lambda segs:segs)
        sender=types.SimpleNamespace(_resolve_groups=lambda result:[1],_send_group=AsyncMock(return_value=True),
            _build_text_fallback=lambda result:['fallback'],_collect_seg_meta=lambda segs:[])
        self.assertTrue(await method(sender,event,None))
        sender._send_group.return_value=False
        self.assertTrue(await method(sender,event,None))
        event.send.side_effect=RuntimeError('send failed')
        self.assertFalse(await method(sender,event,None))
        sender._build_text_fallback=lambda result:[]
        self.assertFalse(await method(sender,event,None))

    async def test_tool_does_not_claim_success_when_sender_failed(self):
        p=PATCHES/'astrbot_plugin_parser/main.py'
        method=extract(p,'llm_parse_media_url',{'logger':logging.getLogger('test')})
        sender=types.SimpleNamespace(parse_and_send=AsyncMock(return_value=False))
        self.assertIn('发送未成功',await method(sender,None,'synthetic'))
        sender.parse_and_send.return_value=True
        self.assertIn('已提交发送',await method(sender,None,'synthetic'))
