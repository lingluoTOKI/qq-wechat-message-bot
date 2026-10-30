"""Manual coverage, safe message sizes and actual free help routing."""
import ast
import importlib.util
import re
import types
import unittest
import tempfile
from pathlib import Path
from test_runtime import function,ROOT

BASE=ROOT/'plugins/astrbot_plugin_dafeiyu_help'
spec=importlib.util.spec_from_file_location('manual_data',BASE/'manual.py')
manual=importlib.util.module_from_spec(spec);spec.loader.exec_module(manual)
ns={'SECTIONS':manual.SECTIONS,'ALIAS':{},'re':re,'MD_LIMIT':3600,'PAGE_LIMIT':2400,'COL':22,
    'Path':Path,'__file__':str(BASE/'main.py'),
    'Comp':types.SimpleNamespace(Plain=lambda text:types.SimpleNamespace(text=text),File=lambda **kw:types.SimpleNamespace(**kw))}
for name,_,_,aliases in manual.SECTIONS:
    for alias in [name]+aliases:
        assert alias.lower() not in ns['ALIAS'] or ns['ALIAS'][alias.lower()]==name,alias
        ns['ALIAS'][alias.lower()]=name
for node in ast.parse((BASE/'main.py').read_text(encoding='utf-8')).body:
    if isinstance(node,ast.FunctionDef):exec(compile(ast.Module(body=[node],type_ignores=[]),'help_functions','exec'),ns)


class CatalogTests(unittest.TestCase):
    def test_essential_current_features_are_documented(self):
        text='\n'.join(ns['_render_section'](i,False) for i in range(len(manual.SECTIONS)))
        for token in ['/搜索','/rvc','/查看翻唱任务','/排行榜','/等待房间','/记忆 画像','/记忆 纠错','/记忆 生日','/记忆 好感度','/考研 授权检查','/考研 同步状态','/表情管理','/lmem status','/bili_sub_test UID','英雄联盟','00:00','05:00','0.5元','不同实例']:
            self.assertIn(token,text)
        self.assertNotIn('私聊的公开发言',text)
        self.assertNotIn('每天 0 点整理前一天',text)

    def test_every_page_and_section_fits_reply_limit(self):
        for markdown in [False,True]:
            self.assertLess(len(ns['_render_all'](markdown)),ns['MD_LIMIT'])
            for i in range(len(manual.SECTIONS)):
                self.assertLess(len(ns['_render_section'](i,markdown)),ns['MD_LIMIT'])
            for page in range(1,len(ns['_full_pages'](markdown))+1):
                self.assertLess(len(ns['_render_full_page'](page,markdown)),ns['MD_LIMIT'])

    def test_pagination_loses_no_lines_or_commands(self):
        for markdown in [False,True]:
            full=ns['_render_full'](markdown)
            paged='\n'.join(ns['_full_pages'](markdown))
            self.assertEqual([x for x in full.splitlines() if x.strip()],[x for x in paged.splitlines() if x.strip()])
            for _,_,rows,_ in manual.SECTIONS:
                for row in rows:
                    if row[0].startswith('/'):
                        self.assertIn(row[0],paged)

    def test_invalid_pages_and_disabled_features_are_explicit(self):
        self.assertIn('共',ns['_render_full_page'](0))
        self.assertIn('共',ns['_render_full_page'](10000))
        text=ns['_render_full'](False)
        self.assertIn('SVCVC',text);self.assertIn('未启用',text)
        commands=[row[0].split()[0] for _,_,rows,_ in manual.SECTIONS for row in rows if row[0].startswith('/')]
        self.assertNotIn('/赛程',commands)

    def test_framework_help_alias_is_not_shadowed(self):
        tree=ast.parse((BASE/'main.py').read_text(encoding='utf-8'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='help_cmd')
        command=next(d for d in node.decorator_list if isinstance(d,ast.Call))
        aliases=next(ast.literal_eval(k.value) for k in command.keywords if k.arg=='alias')
        self.assertNotIn('help',aliases)


class Result:
    def __init__(self,text):self.text=text;self.markdown=False
    def use_markdown(self,value):self.markdown=value;return self

    @classmethod
    def from_chain(cls,parts):
        result=cls(''.join(getattr(part,'text','') for part in parts));result.chain=parts;return result


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self):
        obj=types.SimpleNamespace()
        obj._arg=types.MethodType(function(BASE/'main.py','_arg',{'re':re}),obj)
        obj._reply=types.MethodType(function(BASE/'main.py','_reply',ns),obj)
        obj._file_result=types.MethodType(function(BASE/'main.py','_file_result',ns),obj)
        return obj

    async def run_help(self,text,platform='qq_official'):
        event=types.SimpleNamespace(message_str=text,stop_event=lambda:None,get_platform_name=lambda:platform,plain_result=Result,chain_result=Result.from_chain)
        handler=function(BASE/'main.py','help_cmd',ns)
        replies=[value async for value in handler(self.plugin(),event)]
        self.assertEqual(len(replies),1)
        return replies[0]

    async def test_mentions_aliases_sections_and_paging(self):
        reply=await self.run_help('[At:qq_official] /说明书 全部 2')
        self.assertIn('第2/',reply.text);self.assertTrue(reply.markdown)
        reply=await self.run_help('/功能 考研')
        self.assertIn('/考研 打卡',reply.text)
        reply=await self.run_help('/菜单 生日')
        self.assertIn('00:00',reply.text)

    async def test_plain_fallback_and_invalid_arguments(self):
        reply=await self.run_help('/功能 分页',platform='other')
        self.assertFalse(reply.markdown);self.assertIn('第1/',reply.text)
        reply=await self.run_help('/功能 全部 abc')
        self.assertIn('用法',reply.text)
        reply=await self.run_help('/功能 不存在的类别')
        self.assertIn('未找到分类',reply.text)

    async def test_complete_manual_defaults_to_pdf_and_text_file_is_available(self):
        for command,suffix in [('/说明书','.pdf'),('/功能 全部','.pdf'),('/功能 文件','.pdf'),('/功能 TXT','.txt')]:
            reply=await self.run_help(command)
            files=[part for part in reply.chain if hasattr(part,'file')]
            self.assertEqual(len(files),1);self.assertTrue(files[0].name.endswith(suffix))
            self.assertTrue(Path(files[0].file).is_file())

    async def test_missing_attachment_has_text_navigation_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            method=function(BASE/'main.py','_file_result',{**ns,'__file__':str(Path(tmp)/'main.py')})
            result=method(None,types.SimpleNamespace(plain_result=Result),'pdf')
            self.assertIn('/功能 分页 1',result.text)
