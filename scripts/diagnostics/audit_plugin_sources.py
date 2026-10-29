"""Generate reviewable handler coverage from a local read-only source snapshot."""
import ast
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT/'review/plugin-optimization-20261029/sources'
OUT = ROOT/'docs/插件优化审查-2026-10-29'
DISABLED = {'astrbot_plugin_clonetts','astrbot_plugin_voice_clone_flow','astrbot_plugin_group_guardian','spectrecore'}
PATCHES = ROOT/'plugins/patches/plugin_optimization_20261029'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    commands=collections.defaultdict(list);items=[];syntax=[]
    for plugin in sorted(SNAPSHOT.iterdir()):
        if not plugin.is_dir():continue
        handlers=[];files=list(plugin.rglob('*.py'))
        for original in files:
            rel=original.relative_to(plugin)
            own=ROOT/'plugins'/plugin.name/rel
            patch=PATCHES/plugin.name/rel
            path=own if own.exists() else patch if patch.exists() else original
            try:tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            except SyntaxError as exc:
                syntax.append([plugin.name,str(rel),exc.lineno]);continue
            for n in ast.walk(tree):
                if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
                for d in n.decorator_list:
                    if not isinstance(d,ast.Call):continue
                    name=ast.unparse(d.func)
                    tail=name.rsplit('.',1)[-1]
                    if tail not in {'command','command_group','regex','event_message_type','on_llm_request','on_llm_response','on_decorating_result','llm_tool','on_astrbot_loaded','permission_type','custom_filter'}:continue
                    handlers.append((str(rel),n.lineno,n.name,ast.unparse(d)))
                    if tail=='command':
                        prefix=name.rsplit('.',1)[0] if '.' in name else ''
                        group='' if prefix in {'','filter'} else prefix+' '
                        names=[]
                        if d.args:
                            try:names.append(ast.literal_eval(d.args[0]))
                            except (ValueError,TypeError):pass
                        for kw in d.keywords:
                            if kw.arg=='alias':
                                try:names.extend(ast.literal_eval(kw.value))
                                except (ValueError,TypeError):pass
                        if plugin.name not in DISABLED:
                            for cmd in names:commands[group+str(cmd)].append(plugin.name+':'+n.name)
        items.append((plugin.name,len(files),handlers))
    rows=['# 插件清单与逐入口验证登记','',
          '来源：10月29日线上源码快照和当前本地修复。23个第三方插件目录，19个启用、4个禁用；另有2个框架内置插件。状态已从面板API核对。',
          '静态入口登记不等于输出验证通过。单项已执行测试见验证矩阵，未列为实测通过的入口均待隔离或真实消息验证。',
          '', '| 插件 | 状态 | Python文件数 | 可识别装饰器数 |','|---|---|---:|---:|']
    for p,n,hs in items:rows.append(f'| {p} | {"禁用" if p in DISABLED else "启用"} | {n} | {len(hs)} |')
    rows+=['','## 逐入口登记','', '| 插件 | 文件:行 | 方法 | 入口/过滤条件 | 验证状态 |','|---|---|---|---|---|']
    for p,_,hs in items:
        for file,line,fn,deco in hs:
            deco=deco.replace('|','\\|').replace('\n',' ')
            status='禁用，未调用' if p in DISABLED else '已静态登记；输出验证按矩阵执行'
            rows.append(f'| {p} | {file}:{line} | {fn} | `{deco}` | {status} |')
    rows+=['','动态注册的FunctionTool和框架内置入口另行核对，不能仅凭装饰器数作为全部功能分母。','',
           '## 静态命令候选冲突','']
    duplicates={k:sorted(set(v)) for k,v in commands.items() if len(set(v))>1}
    rows.append('当前快照的启用插件中未检测到同一限定命令名的重复注册。内置 /help 与说明书别名冲突已通过移除说明书英文别名处理。' if not duplicates else json.dumps(duplicates,ensure_ascii=False,indent=2))
    rows+=['','AST解析失败：'+json.dumps(syntax,ensure_ascii=False),
           '','复现：`D:\\Anaconda3\\python.exe scripts/diagnostics/audit_plugin_sources.py`。依赖本机保留的只读源码快照，不访问服务器。']
    (OUT/'插件清单与入口登记.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print('plugins',len(items),'decorators',sum(len(h) for _,_,h in items),'syntax errors',len(syntax),'duplicates',len(duplicates))


if __name__=='__main__':main()
