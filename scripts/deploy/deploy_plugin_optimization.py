"""Stage isolated checks, back up drift-checked files, then apply one bot restart."""
import hashlib
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
import paramiko

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'review/plugin-optimization-20261029'
BASE='/opt/bot-stack/astrbot/data'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup='/opt/bot-stack/backups/plugin-optimization-'+stamp
c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=12)

def run(command,code=None,timeout=90):
    inp,out,err=c.exec_command(command,timeout=timeout)
    if code is not None:inp.write(code);inp.channel.shutdown_write()
    result,error=out.read().decode(errors='replace'),err.read().decode(errors='replace')
    if out.channel.recv_exit_status():raise RuntimeError(result+error)
    return result

def remote_python(code):return run('python3 -',code)

sftp=c.open_sftp()
files={}
groups={
 'astrbot_plugin_wushu_ops':['main.py','assistant_runtime.py','metadata.yaml'],
 'astrbot_plugin_dafeiyu_daily_memory':['main.py','birthday_watch.py','store.py','metadata.yaml'],
 'astrbot_plugin_dafeiyu_help':['main.py','metadata.yaml'],
 'astrbot_plugin_dafeiyu_cover':['main.py','metadata.yaml'],
 'astrbot_plugin_usage_view':['main.py','metadata.yaml']}
for plugin,names in groups.items():
    for name in names:files[BASE+'/plugins/'+plugin+'/'+name]=ROOT/'plugins'/plugin/name
patches=ROOT/'plugins/patches/plugin_optimization_20261029'
for file in patches.rglob('*.py'):files[BASE+'/plugins/'+file.relative_to(patches).as_posix()]=file
for file in (ROOT/'plugins/dafeiyu_runtime').glob('*.py'):files[BASE+'/pylibs/dafeiyu_runtime/'+file.name]=file
files['/opt/bot-stack/bin/bot_event_audit.py']=ROOT/'scripts/diagnostics/bot_event_audit.py'
originals=json.loads((REVIEW/'source-hashes.json').read_text())
manifest=[]
for target,source in files.items():
    if target.startswith(BASE+'/plugins/'):
        key=target[len(BASE+'/plugins/'):];expected=originals[key]
    else:expected=None
    manifest.append({'target':target,'source':str(source.relative_to(ROOT)).replace('\\','/'),
                     'before':expected,'after':hashlib.sha256(source.read_bytes()).hexdigest()})
print('Target files',len(manifest),'Backup',backup,flush=True)
run('mkdir -p '+shlex.quote(backup+'/workspace'))
stage=backup+'/workspace'

def upload(source,relative):
    target=stage+'/'+relative
    run('mkdir -p '+shlex.quote(str(Path(target).parent).replace('\\','/')))
    sftp.put(str(source),target)

for m in manifest:upload(ROOT/m['source'],m['source'])
print(remote_python('''
import json,pathlib
manifest=json.loads('''+repr(json.dumps(manifest))+''')
base=pathlib.Path('''+repr(stage)+''')
for m in manifest:
 p=base/m['source']
 if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
print('All staged Python sources compile.')
'''),flush=True)
upload(ROOT/'tests/plugin_optimization/test_runtime.py','tests/plugin_optimization/test_runtime.py')
# Existing network/room regression tests and the formatter fixture read canonical
# workspace paths. No production modules are initialized by these unit tests.
for file in (ROOT/'plugins/astrbot_plugin_wushu_ops').rglob('*.py'):
    upload(file,file.relative_to(ROOT).as_posix())
formatter=patches/'astrbot_plugin_livingmemory/core/utils/formatting.py'
upload(formatter,'review/plugin-optimization-20261029/sources/astrbot_plugin_livingmemory/core/utils/formatting.py')
container_stage='/tmp/plugin-opt-stage-'+stamp
run('docker exec astrbot mkdir -p '+shlex.quote(container_stage))
run('docker cp '+shlex.quote(stage+'/.')+' astrbot:'+shlex.quote(container_stage))
run('docker exec -e PYTHONPATH='+shlex.quote(container_stage+'/plugins:/AstrBot/data/pylibs')+' -w '+shlex.quote(container_stage)+' astrbot python -m unittest discover -s tests/plugin_optimization -q')
print('New regression tests passed in container.',flush=True)
print(run('docker exec -e PYTHONPATH='+shlex.quote(container_stage+'/plugins:/AstrBot/data/pylibs')+' -w '+shlex.quote(container_stage)+' astrbot python -m unittest discover -s '+shlex.quote('plugins/astrbot_plugin_wushu_ops/tests')+' -q'),flush=True)
if '--apply' not in sys.argv:
    print('Staging only. Production unchanged.');c.close();sys.exit(0)

payload=json.dumps(manifest,ensure_ascii=True)
before=run('systemctl show stronghold stronghold-3001 -p MainPID -p ActiveState')
code=f'''
import pathlib,hashlib,json,shutil,sqlite3,os
os.umask(0o077)
manifest=json.loads({payload!r});base=pathlib.Path({backup!r});workspace=base/'workspace'
for m in manifest:
 p=pathlib.Path(m['target'])
 if m['before'] is not None:
  assert hashlib.sha256(p.read_bytes()).hexdigest()==m['before'], 'Live source drift: '+str(p)
 else:
  assert not p.exists(), 'New destination already exists: '+str(p)
 staged=workspace/m['source']
 assert hashlib.sha256(staged.read_bytes()).hexdigest()==m['after']
for m in manifest:
 p=pathlib.Path(m['target'])
 if p.exists():
  dest=base/'original'/p.relative_to('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
db=pathlib.Path('{BASE}/plugin_data/astrbot_plugin_dafeiyu_daily_memory/memory.db')
if db.exists():
 src=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);dst=sqlite3.connect(str(base/'memory-before.db'))
 src.backup(dst);dst.close();src.close()
(base/'manifest.json').write_text(json.dumps(manifest,indent=2))
for m in manifest:
 p=pathlib.Path(m['target']);p.parent.mkdir(parents=True,exist_ok=True)
 temp=p.with_name(p.name+'.optimization-upload');shutil.copy2(workspace/m['source'],temp);temp.replace(p)
print('Backups and hashes verified; files installed.')
'''
print(remote_python(code),flush=True)
# Generated rollback restores source files only. New additive receipt tables are
# harmless to older code; never overwrite an active chat DB to roll code back.
rollback=f'''
import json,pathlib,shutil,subprocess
base=pathlib.Path({backup!r})
for item in json.loads((base/'manifest.json').read_text()):
 p=pathlib.Path(item['target']);old=base/'original'/p.relative_to('/')
 if item['before'] is not None:shutil.copy2(old,p)
 elif p.exists():p.unlink()
cron=pathlib.Path('/etc/cron.d/astrbot-plugin-events')
if cron.exists():cron.unlink()
subprocess.run(['docker','restart','astrbot'],check=True,timeout=90)
'''
with sftp.open(backup+'/rollback.py','w') as f:f.write(rollback)
print(run('docker restart astrbot',timeout=90),flush=True)
after=run('systemctl show stronghold stronghold-3001 -p MainPID -p ActiveState')
assert before==after,'Game process changed'
print('Game PIDs unchanged.',flush=True)
record={'backup':backup,'files':manifest,'game_pids_unchanged':True,'restart_requested':True}
(REVIEW/'deployment.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
c.close()
