"""Deploy the complete manual after routing/length and framework smoke checks."""
import hashlib,json,tarfile,time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
NAME='astrbot_plugin_dafeiyu_help';SOURCE=ROOT/'plugins'/NAME


def main():
    archive=ROOT/'review/help-complete-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name in ['main.py','manual.py','metadata.yaml']:
            p=SOURCE/name
            if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
            t.add(p,arcname='plugins/'+NAME+'/'+name)
        t.add(ROOT/'tests/plugin_optimization/test_help_manual.py',arcname='tests/plugin_optimization/test_help_manual.py')
    c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        i,o,e=c.exec_command('docker exec -i astrbot python -',timeout=150)
        i.write(code);i.channel.shutdown_write();out=o.read().decode();err=e.read().decode()
        if o.channel.recv_exit_status():raise RuntimeError(out+err)
        print(out,flush=True)
    try:
        with c.open_sftp() as s:s.put(str(archive),'/opt/bot-stack/astrbot/data/temp/help-complete-tests.tar.gz')
        run('''import asyncio,importlib,json,pathlib,subprocess,sys,tarfile
from types import SimpleNamespace
stage=pathlib.Path('/tmp/profile-quality-acceptance')
with tarfile.open('/AstrBot/data/temp/help-complete-tests.tar.gz') as t:t.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/help-complete-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-450:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'))
m=importlib.import_module('astrbot_plugin_dafeiyu_help.main')
from astrbot.api.event import MessageEventResult
async def smoke():
 plugin=m.DafeiyuHelpPlugin(SimpleNamespace())
 samples=['/功能','/说明书 记忆','[At:qq_official] /菜单 生日','/功能 全部 0','/功能 全部 abc']
 samples+=['/功能 '+name for name,_,_,_ in m.SECTIONS]
 pages=len(m._full_pages(True));samples+=['/功能 全部 '+str(i) for i in range(1,pages+1)]
 for command in samples:
  event=SimpleNamespace(message_str=command,stop_event=lambda:None,get_platform_name=lambda:'qq_official',plain_result=lambda text:MessageEventResult().message(text))
  replies=[reply async for reply in plugin.help_cmd(event)]
  assert len(replies)==1
  text=''.join(getattr(part,'text','') for part in replies[0].chain)
  assert text and len(text)<m.MD_LIMIT,(command,len(text))
 report={'categories':len(m.SECTIONS),'full_pages':pages,'framework_routes_checked':len(samples),'paid_calls':0,'qq_test_sends':0}
 pathlib.Path('/AstrBot/data/temp/help-complete-framework.json').write_text(json.dumps(report))
 print('Actual framework routing:',json.dumps(report))
asyncio.run(smoke())
''')
        backup='/opt/bot-stack/backups/help-complete-'+time.strftime('%Y%m%d-%H%M%S')
        manifest={'backup':backup,'files':[],'version':'1.9.0'}
        with c.open_sftp() as s:
            s.mkdir(backup,mode=0o700)
            for name,snapshot in [('main.py','help-live-before.py'),('manual.py',None),('metadata.yaml','help-live-before.yaml')]:
                target='/opt/bot-stack/astrbot/data/plugins/'+NAME+'/'+name
                try:
                    with s.open(target,'rb') as f:before=f.read()
                except FileNotFoundError:before=None
                expected=(ROOT/'review/help-source-snapshot'/snapshot).read_bytes() if snapshot else None
                assert before==expected,'Live help source changed: '+name
                row={'target':target,'before':hashlib.sha256(before).hexdigest() if before is not None else None,
                     'after':hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),'backup':backup+'/'+name+'.before'}
                if before is not None:
                    with s.open(row['backup'],'wb') as f:f.write(before)
                manifest['files'].append(row)
            for row in manifest['files']:
                name=Path(row['target']).name
                s.put(str(SOURCE/name),row['target']+'.manual-new');s.posix_rename(row['target']+'.manual-new',row['target'])
                with s.open(row['target'],'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==row['after']
            for name in ['tests.log','framework.json']:
                s.get('/opt/bot-stack/astrbot/data/temp/help-complete-'+name,str(ROOT/'review'/('help-complete-'+name)))
        (ROOT/'review/help-complete-deployment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        reload_code='''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':'astrbot_plugin_dafeiyu_help'}).encode(),headers=headers)
assert json.load(urllib.request.urlopen(r,timeout=90)).get('status')=='ok'
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name=astrbot_plugin_dafeiyu_help',headers=headers)
x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated'] and x['version']=='1.9.0'
print('Help plugin 1.9.0 activated')
'''
        run(reload_code)
        rollback='''import hashlib,json,subprocess
from pathlib import Path
m=json.loads(MANIFEST)
for row in m['files']:
 assert hashlib.sha256(Path(row['target']).read_bytes()).hexdigest()==row['after'],row['target']
 if row['before']:assert hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest()==row['before'],row['backup']
for row in reversed(m['files']):
 if row['before']:Path(row['target']).write_bytes(Path(row['backup']).read_bytes())
 else:Path(row['target']).unlink()
subprocess.run(['docker','exec','-i','astrbot','python','-'],input=RELOAD,text=True,check=True)
print('Help restored to previous version; unrelated plugins untouched')
'''.replace('MANIFEST',repr(json.dumps(manifest))).replace('RELOAD',repr(reload_code.replace("x['version']=='1.9.0'","x['version']=='1.8.1'").replace('Help plugin 1.9.0 activated','Help plugin 1.8.1 restored')))
        compile(rollback,'rollback_help.py','exec')
        with c.open_sftp() as s:
            with s.open(backup+'/rollback_help.py','w') as f:f.write(rollback)
        print('Verified deployed files:',len(manifest['files']),'Backup:',backup,'Rollback not executed')
    finally:c.close()


if __name__=='__main__':main()
