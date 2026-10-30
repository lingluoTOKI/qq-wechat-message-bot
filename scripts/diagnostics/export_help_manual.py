"""Export the same catalog used in QQ and audit current command coverage."""
import ast
import importlib.util
import json
from pathlib import Path

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
BASE=ROOT/'plugins/astrbot_plugin_dafeiyu_help'


def main():
    spec=importlib.util.spec_from_file_location('manual_data',BASE/'manual.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    content=['# 大肥鱼（小洛）完整说明书','','更新：2026年10月30日 · QQ官方机器人 · 说明书v1.10.2。','',
             '群里@机器人，私聊直接说话。`/说明书` 或 `/功能 全部` 发送PDF，`/功能 TXT` 发送文字附件；分类入口 `/功能 分类名`，文字浏览 `/功能 分页 页码`。','',
             '本说明书按当前启用插件的实际入口与配置整理；需账号、授权、音源和后端的功能，以实际返回状态为准。说明书和规则查询不调用聊天模型。','',
             '## 分类目录','']
    for name,_,_,_ in m.SECTIONS:content.append('- `/功能 '+name+'`')
    for name,title,rows,_ in m.SECTIONS:
        content+=['','## '+name+' · '+title,'']
        for row in rows:
            cmd,desc=row[:2];content.append('- '+('`'+cmd+'`：' if cmd else '')+desc)
            if len(row)>2 and row[2]:content.append('  - '+row[2])
    content+=['','## 验证范围','',
        '命令入口、参数说明、分类跳转、分页完整性及消息长度经过核对；未为了编写说明书实际触发付费模型、群通知、打卡、踢人、重启或清理操作。后端外部服务的实时可用性仍以实际调用为准。','']
    (ROOT/'docs/大肥鱼完整说明书.md').write_text('\n'.join(content),encoding='utf-8')
    inventory_path=ROOT/'review/help-current-inventory.json'
    if not inventory_path.exists():
        print('Manual exported; live inventory not available for coverage audit');return
    inventory=json.loads(inventory_path.read_text(encoding='utf-8'))
    commands=[]
    parents={'lmem':'lmem','bili':'bili','meme_manager':'表情管理'}
    for row in inventory['commands']:
        prefix=row['decorator'].split('.command',1)[0]
        full=(parents[prefix]+' ' if prefix in parents else '')+row['command']
        alias_prefix=parents[prefix]+' ' if prefix in parents else ''
        commands.append({'plugin':row['plugin'],'command':'/'+full,'aliases':['/'+alias_prefix+a for a in row.get('aliases',[])]})
    source=ROOT/'review/help-source-snapshot/bili.py'
    if source.exists():
        for n in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            for d in n.decorator_list:
                if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='command':
                    aliases=[]
                    for keyword in d.keywords:
                        if keyword.arg=='alias':aliases=['/'+x for x in ast.literal_eval(keyword.value)]
                    commands.append({'plugin':'astrbot_plugin_bilibili','command':'/'+ast.literal_eval(d.args[0]),'aliases':aliases})
    for cmd in ['help','sid','name','reset','stop','new','stats','provider','set','unset','dashboard_update']:
        commands.append({'plugin':'builtin_commands','command':'/'+cmd})
    excluded={
        '/svcvc':'SVCVC后端当前未启用','/qqsvcvc':'SVCVC后端当前未启用',
        '/列出svcvc分离模型':'SVCVC后端当前未启用','/切换svcvc分离模型':'SVCVC后端当前未启用',
        '/刷新svcvc音色':'SVCVC后端当前未启用','/设置svcvc后端链接':'SVCVC后端当前未启用',
        '/本地翻唱':'该分支当前未启用，上传音频改用已启用的 /翻唱',
        '/翻唱选择':'内部选歌按钮回调，不作为独立用户命令',
        '/set':'框架高级会话变量配置，非日常功能；入口见 /help',
        '/unset':'框架高级会话变量配置，非日常功能；入口见 /help',
        '/dashboard_update':'框架WebUI升级维护，非日常机器人功能'}
    documented=[row[0].lower() for _,_,rows,_ in m.SECTIONS for row in rows if row[0].startswith('/')]
    missing=[];audit=[]
    for row in commands:
        cmd=row['command'];covered=any(x==variant.lower() or x.startswith(variant.lower()+' ') for variant in [cmd]+row.get('aliases',[]) for x in documented)
        reason=excluded.get(cmd,'')
        if not covered and not reason:missing.append(row)
        audit.append({**row,'documented':covered,'excluded_reason':reason if not covered else ''})
    report={'categories':len(m.SECTIONS),'catalog_command_examples':len(documented),
            'registered_entries_checked':len(audit),'missing':missing,'commands':audit,
            'plugins':[{'name':p['name'],'version':p.get('version'),'activated':p.get('activated')} for p in inventory['plugins']]}
    (ROOT/'review/help-command-coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if missing:raise RuntimeError('Undocumented usable command entries: '+json.dumps(missing,ensure_ascii=False))
    print(json.dumps({k:v for k,v in report.items() if k not in ['commands','plugins']},ensure_ascii=False))


if __name__=='__main__':main()
