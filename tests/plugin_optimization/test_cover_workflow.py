import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'plugins/astrbot_plugin_dafeiyu_cover/main.py'


def method(name):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    ns = {'logger': logging.getLogger('test'), 'AstrMessageEvent': object}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns[name]


class CoverWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_does_not_interrupt_download_or_final_media(self):
        sent = []
        stages = []
        async def send(result):
            sent.append(result)
            # A decoration hook could stop the pipeline after the first yield.
            event.stopped = True
        event = SimpleNamespace(send=send, stopped=False, plain_result=lambda x: x)
        async def workflow():
            try:
                yield 'downloading'
                stages.append('download')
                await asyncio.sleep(0)
                stages.append('convert')
                yield 'audio'
            finally:
                stages.append('cleanup')
        await method('_send_workflow')(None, event, workflow())
        self.assertEqual(sent, ['downloading', 'audio'])
        self.assertEqual(stages, ['download', 'convert', 'cleanup'])

    async def test_compact_command_dispatches_once(self):
        sent = []
        def stop(): sent.append('stop')
        async def song_cmd(event):
            sent.append(event.message_str)
            yield 'candidates'
        event = SimpleNamespace(message_str='/点歌sincerely', stop_event=stop)
        obj = SimpleNamespace(song_cmd=song_cmd)
        results = [r async for r in method('compact_song_cmd')(obj, event)]
        self.assertEqual(results, ['candidates'])
        self.assertEqual(sent, ['stop', '/点歌sincerely'])
