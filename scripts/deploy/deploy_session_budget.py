"""Apply the approved private-unlimited / per-group 0.5 CNY daily policy."""
import hashlib
import json
import tarfile
import time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
BASE='/opt/bot-stack/astrbot/data/'
FILES={
 'plugins/dafeiyu_runtime/usage.py':'pylibs/dafeiyu_runtime/usage.py',
 'plugins/dafeiyu_runtime/session_budget.py':'pylibs/dafeiyu_runtime/session_budget.py',
 'plugins/astrbot_plugin_request_ledger/main.py':'plugins/astrbot_plugin_request_ledger/main.py',
 'plugins/astrbot_plugin_usage_view/main.py':'plugins/astrbot_plugin_usage_view/main.py',
 'plugins/dafeiyu_group_guard/main.py':'plugins/astrbot_plugin_dafeiyu_group_guard/main.py',
 'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py':'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py',
 'plugins/patches/plugin_optimization_20261029/astrbot_plugin_proactive_chat/core/chat_flow.py':'plugins/astrbot_plugin_proactive_chat/core/chat_flow.py',
 'plugins/patches/plugin_optimization_20261029/astrbot_plugin_proactive_chat/main.py':'plugins/astrbot_plugin_proactive_chat/main.py',
}


def main():
    archive=ROOT/'review/session-budget-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name in ['vendor/astrbot/astrbot_plugin_lol_esports/main.py','scripts/diagnostics/bot_event_audit.py']:
            tar.add(ROOT/name,arcname=name)
        for folder in ['plugins','tests/plugin_optimization']:
            for p in (ROOT/folder).rglob('*'):
                if p.is_file() and p.suffix in ('.py','.json','.yaml','.txt'):
                    tar.add(p,arcname=p.relative_to(ROOT))
    client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        inp,out,err=client.exec_command('docker exec -i astrbot python -',timeout=180)
        inp.write(code);inp.channel.shutdown_write();output,error=out.read().decode(),err.read().decode()
        if out.channel.recv_exit_status():raise RuntimeError(output+error)
        print(output,flush=True)
    try:
        with client.open_sftp() as s:s.put(str(archive),BASE+'temp/session-budget-tests.tar.gz')
        run('''import importlib,pathlib,subprocess,sys,tarfile
stage=pathlib.Path('/tmp/session-budget-acceptance');stage.mkdir(exist_ok=True)
with tarfile.open('/AstrBot/data/temp/session-budget-tests.tar.gz') as tar:tar.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/session-budget-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-900:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'))
m=importlib.import_module('astrbot_plugin_request_ledger.main')
assert hasattr(m.RequestLedgerPlugin,'remember_budget_origin')
print('Actual framework import and ALL-message scope hook registration passed')
''')
        backup='/opt/bot-stack/backups/session-budget-'+time.strftime('%Y%m%d-%H%M%S')
        manifest={'backup':backup,'files':[],'private_unlimited':True,'per_group_daily_cny':.5}
        with client.open_sftp() as s:
            s.mkdir(backup,mode=0o700)
            sources={BASE+dest:(ROOT/src).read_bytes() for src,dest in FILES.items()}
            config_path=BASE+'plugin_data/astrbot_plugin_cost_control/config.json'
            with s.open(config_path,'rb') as f:original_config=f.read()
            cfg=json.loads(original_config.decode('utf-8-sig'))
            for section in ['budgets','budgets_cost']:
                for key in cfg.get(section,{}):cfg[section][key]=0
            sources[config_path]=json.dumps(cfg,ensure_ascii=False,indent=2).encode()
            originals={}
            for i,(target,data) in enumerate(sources.items()):
                try:
                    with s.open(target,'rb') as f:before=f.read()
                except FileNotFoundError:before=None
                originals[target]=before
                row={'target':target,'before':hashlib.sha256(before).hexdigest() if before is not None else None,
                     'after':hashlib.sha256(data).hexdigest(),'backup':backup+f'/{i}.before'}
                if before is not None:
                    with s.open(row['backup'],'wb') as f:f.write(before)
                    s.chmod(row['backup'],0o600)
                manifest['files'].append(row)
            assert originals[config_path]==original_config,'Config changed during preparation'
            for row in manifest['files']:
                target=row['target']
                try:
                    with s.open(target,'rb') as f:live=f.read()
                except FileNotFoundError:live=None
                assert live==originals[target],'Live source changed during preparation: '+target
            for row in manifest['files']:
                target=row['target'];temp=target+'.session-budget-new'
                with s.open(temp,'wb') as f:f.write(sources[target])
                s.chmod(temp,0o600 if target==config_path else 0o644)
                s.posix_rename(temp,target)
                with s.open(target,'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==row['after']
            s.get(BASE+'temp/session-budget-tests.log',str(ROOT/'review/session-budget-tests.log'))
        (ROOT/'review/session-budget-deployment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        run('''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+300},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
for name in ['astrbot_plugin_request_ledger','astrbot_plugin_cost_control','astrbot_plugin_dafeiyu_group_guard','astrbot_plugin_dafeiyu_daily_memory','astrbot_plugin_proactive_chat','astrbot_plugin_usage_view']:
 r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':name}).encode(),headers=headers)
 x=json.load(urllib.request.urlopen(r,timeout=90));assert x.get('status')=='ok',name
 print('Reloaded:',name)
 r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name='+name,headers=headers)
 assert json.load(urllib.request.urlopen(r,timeout=30))['data']['activated'],name
''')
        print('Scoped source and config files verified:',len(manifest['files']),'Backup:',backup)
    finally:client.close()


if __name__=='__main__':main()
