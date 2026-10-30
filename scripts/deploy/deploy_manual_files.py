"""Bundle validated manual files and switch complete-help replies to attachments."""
import hashlib,json,tarfile,time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
NAME='astrbot_plugin_dafeiyu_help';SOURCE=ROOT/'plugins'/NAME
NAMES=['main.py','manual.py','metadata.yaml','resources/大肥鱼完整说明书.pdf','resources/大肥鱼完整说明书.txt']


def main():
    previous=json.loads((ROOT/'review/help-complete-deployment.json').read_text(encoding='utf-8'))
    expected={row['target']:row['after'] for row in previous['files']}
    archive=ROOT/'review/manual-file-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name in NAMES:t.add(SOURCE/name,arcname='plugins/'+NAME+'/'+name)
        t.add(ROOT/'tests/plugin_optimization/test_help_manual.py',arcname='tests/plugin_optimization/test_help_manual.py')
    c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        i,o,e=c.exec_command('docker exec -i astrbot python -',timeout=150)
        i.write(code);i.channel.shutdown_write();out=o.read().decode();err=e.read().decode()
        if o.channel.recv_exit_status():raise RuntimeError(out+err)
        print(out,flush=True)
    try:
        with c.open_sftp() as s:s.put(str(archive),'/opt/bot-stack/astrbot/data/temp/manual-file-tests.tar.gz')
        run('''import asyncio,importlib,json,pathlib,subprocess,sys,tarfile
from types import SimpleNamespace
stage=pathlib.Path('/tmp/profile-quality-acceptance')
with tarfile.open('/AstrBot/data/temp/manual-file-tests.tar.gz') as t:t.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/manual-file-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-450:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'))
m=importlib.import_module('astrbot_plugin_dafeiyu_help.main')
from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import File
async def smoke():
 plugin=m.DafeiyuHelpPlugin(SimpleNamespace());checked=0
 for command,suffix in [('/说明书','.pdf'),('/功能 全部','.pdf'),('/功能 文件','.pdf'),('/功能 TXT','.txt'),('[At:qq_official] /说明书','.pdf')]:
  event=SimpleNamespace(message_str=command,stop_event=lambda:None,get_platform_name=lambda:'qq_official',plain_result=lambda text:MessageEventResult().message(text),chain_result=lambda parts:MessageEventResult(chain=parts))
  replies=[reply async for reply in plugin.help_cmd(event)];assert len(replies)==1
  files=[part for part in replies[0].chain if isinstance(part,File)]
  assert len(files)==1 and files[0].name.endswith(suffix)
  assert pathlib.Path(files[0].file_).is_file();checked+=1
 print('Actual framework file-result routes:',checked,'No upload or QQ send performed')
asyncio.run(smoke())
''')
        backup='/opt/bot-stack/backups/manual-files-'+time.strftime('%Y%m%d-%H%M%S');m={'backup':backup,'files':[],'version':'1.9.1'}
        with c.open_sftp() as s:
            s.mkdir(backup,mode=0o700)
            for index,name in enumerate(NAMES):
                target='/opt/bot-stack/astrbot/data/plugins/'+NAME+'/'+name
                try:
                    with s.open(target,'rb') as f:before=f.read()
                except FileNotFoundError:before=None
                sha=hashlib.sha256(before).hexdigest() if before is not None else None
                assert sha==expected.get(target),'Live source changed: '+name
                row={'target':target,'before':sha,'after':hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),'backup':backup+f'/{index}.before'}
                if before is not None:
                    with s.open(row['backup'],'wb') as f:f.write(before)
                m['files'].append(row)
            folder='/opt/bot-stack/astrbot/data/plugins/'+NAME+'/resources'
            try:s.stat(folder)
            except FileNotFoundError:s.mkdir(folder)
            for name,row in zip(NAMES,m['files']):
                s.put(str(SOURCE/name),row['target']+'.manual-file-new');s.posix_rename(row['target']+'.manual-file-new',row['target'])
                with s.open(row['target'],'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==row['after']
            s.get('/opt/bot-stack/astrbot/data/temp/manual-file-tests.log',str(ROOT/'review/manual-file-tests.log'))
        (ROOT/'review/manual-files-deployment.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
        reload_code='''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':'astrbot_plugin_dafeiyu_help'}).encode(),headers=headers)
assert json.load(urllib.request.urlopen(r,timeout=90)).get('status')=='ok'
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name=astrbot_plugin_dafeiyu_help',headers=headers)
x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated'] and x['version']=='1.9.1'
print('Help plugin 1.9.1 active; PDF and TXT packaged')
'''
        run(reload_code)
        rollback='''import hashlib,json,subprocess
from pathlib import Path
m=json.loads(MANIFEST)
for row in m['files']:
 assert hashlib.sha256(Path(row['target']).read_bytes()).hexdigest()==row['after']
 if row['before']:assert hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest()==row['before']
for row in reversed(m['files']):
 if row['before']:Path(row['target']).write_bytes(Path(row['backup']).read_bytes())
 else:Path(row['target']).unlink()
subprocess.run(['docker','exec','-i','astrbot','python','-'],input=RELOAD,text=True,check=True)
'''.replace('MANIFEST',repr(json.dumps(m))).replace('RELOAD',repr(reload_code.replace("x['version']=='1.9.1'","x['version']=='1.9.0'").replace('Help plugin 1.9.1 active; PDF and TXT packaged','Previous paged help restored')))
        compile(rollback,'rollback_manual_files.py','exec')
        with c.open_sftp() as s:
            with s.open(backup+'/rollback_manual_files.py','w') as f:f.write(rollback)
        print('Verified files:',len(m['files']),'Backup:',backup,'Rollback not run')
    finally:c.close()


if __name__=='__main__':main()
