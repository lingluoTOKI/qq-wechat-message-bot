"""Deploy request accounting with isolated tests, source guards and scoped install."""
import hashlib
import io
import json
import shlex
import sys
import zipfile
from datetime import datetime
from pathlib import Path
import paramiko

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'review/request-ledger-20261029';REVIEW.mkdir(parents=True,exist_ok=True)
BASE='/opt/bot-stack/astrbot/data'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup='/opt/bot-stack/backups/request-ledger-'+stamp
stage=backup+'/workspace';container_stage='/tmp/request-ledger-stage-'+stamp
c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=12)
sftp=c.open_sftp()

def run(command,code=None,timeout=90):
    inp,out,err=c.exec_command(command,timeout=timeout)
    if code is not None:inp.write(code);inp.channel.shutdown_write()
    result,error=out.read().decode(errors='replace'),err.read().decode(errors='replace')
    if out.channel.recv_exit_status():raise RuntimeError(result+error)
    return result

def upload(source,relative):
    target=stage+'/'+relative
    run('mkdir -p '+shlex.quote(target.rsplit('/',1)[0]))
    sftp.put(str(source),target)

run('mkdir -p '+shlex.quote(stage))
for file in (ROOT/'plugins/dafeiyu_runtime').glob('*.py'):upload(file,file.relative_to(ROOT).as_posix())
for file in (ROOT/'plugins/astrbot_plugin_request_ledger').glob('*'):
    if file.is_file():upload(file,file.relative_to(ROOT).as_posix())
for rel in ['plugins/astrbot_plugin_usage_view/main.py','plugins/astrbot_plugin_usage_view/metadata.yaml',
            'scripts/cost/import_official_usage.py','scripts/diagnostics/bot_event_audit.py','tests/request_ledger/test_ledger.py',
            'scripts/cost/reconcile_request_ledger.py',
            'tests/request_ledger/test_sdk_integration.py','tests/plugin_optimization/test_runtime.py',
            'plugins/astrbot_plugin_wushu_ops/main.py','plugins/astrbot_plugin_wushu_ops/assistant_runtime.py',
            'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py','plugins/astrbot_plugin_dafeiyu_cover/main.py',
            'plugins/patches/plugin_optimization_20261029/astrbot_plugin_livingmemory/core/utils/formatting.py']:
    upload(ROOT/rel,rel)
run('docker exec astrbot mkdir -p '+container_stage)
run('docker cp '+shlex.quote(stage+'/.')+' astrbot:'+container_stage)
env='docker exec -e PYTHONPATH='+shlex.quote(container_stage+'/plugins:/AstrBot/data/pylibs')+' -w '+container_stage+' astrbot '
run(env+'python -m unittest discover -s tests/request_ledger -q')
run(env+'python -m unittest discover -s tests/plugin_optimization -q')
print('Container: request-ledger/SDK and prior regressions passed.',flush=True)
if '--apply' not in sys.argv:
    print('Staging only:',backup);c.close();sys.exit(0)

old=json.loads((ROOT/'review/plugin-optimization-20261029/deployment.json').read_text(encoding='utf-8'))
previous={m['target']:m['after'] for m in old['files']}
mapping={
 BASE+'/pylibs/dafeiyu_runtime/usage.py':'plugins/dafeiyu_runtime/usage.py',
 BASE+'/pylibs/dafeiyu_runtime/request_ledger.py':'plugins/dafeiyu_runtime/request_ledger.py',
 BASE+'/pylibs/dafeiyu_runtime/request_capture.py':'plugins/dafeiyu_runtime/request_capture.py',
 BASE+'/plugins/astrbot_plugin_usage_view/main.py':'plugins/astrbot_plugin_usage_view/main.py',
 BASE+'/plugins/astrbot_plugin_usage_view/metadata.yaml':'plugins/astrbot_plugin_usage_view/metadata.yaml',
 '/opt/bot-stack/bin/import_official_usage.py':'scripts/cost/import_official_usage.py',
 '/opt/bot-stack/bin/reconcile_request_ledger.py':'scripts/cost/reconcile_request_ledger.py'}
manifest=[]
for target,rel in mapping.items():
    manifest.append({'target':target,'source':rel,'before':previous.get(target),
                     'after':hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()})
archive=io.BytesIO()
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in (ROOT/'plugins/astrbot_plugin_request_ledger').glob('*'):
        if p.is_file():z.writestr(p.name,p.read_bytes())
with sftp.open(backup+'/request-ledger-plugin.zip','wb') as f:f.write(archive.getvalue())
export_path=Path(r'D:\download\usage_data_2026-10-29_2026-10-29.zip')
sftp.put(str(export_path),backup+'/official-private.zip');sftp.chmod(backup+'/official-private.zip',0o600)
before=run('systemctl show stronghold stronghold-3001 -p MainPID -p ActiveState')
payload=json.dumps(manifest)
code=f'''
import pathlib,hashlib,json,shutil,os,zipfile,io,csv
os.umask(0o077)
base=pathlib.Path({backup!r});manifest=json.loads({payload!r})
assert not pathlib.Path('{BASE}/plugins/astrbot_plugin_request_ledger').exists(), 'Plugin exists'
for m in manifest:
 p=pathlib.Path(m['target'])
 if m['before'] is None:assert not p.exists(), 'New destination exists: '+str(p)
 else:assert hashlib.sha256(p.read_bytes()).hexdigest()==m['before'], 'Source drift: '+str(p)
 source=base/'workspace'/m['source'];assert hashlib.sha256(source.read_bytes()).hexdigest()==m['after']
 if source.suffix=='.py':compile(source.read_bytes(),str(source),'exec')
for m in manifest:
 p=pathlib.Path(m['target'])
 if p.exists():
  b=base/'original'/p.relative_to('/');b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,b)
(base/'manifest.json').write_text(json.dumps(manifest,indent=2))
# Verify exported masked key matches configured DeepSeek key without printing it.
cfg=json.load(open('{BASE}/cmd_config.json',encoding='utf-8-sig'))
keys=[]
for item in cfg.get('provider_sources',[]):
 if item.get('id')=='deepseek':
  value=item.get('key',[])
  keys.extend(value if isinstance(value,list) else [value])
masks=set()
with zipfile.ZipFile(base/'official-private.zip') as z:
 for n in z.namelist():
  if n.startswith('amount') and n.endswith('.csv'):
   for r in csv.DictReader(io.StringIO(z.read(n).decode('utf-8-sig'))):masks.add(r['api_key'])
assert len(masks)==1 and any(isinstance(k,str) and k.startswith(next(iter(masks)).split('*')[0]) and k.endswith(next(iter(masks)).split('*')[-1]) for k in keys), 'Export key scope does not match configured provider'
print('Export scope matches configured key; credentials omitted.')
for m in manifest:
 p=pathlib.Path(m['target']);p.parent.mkdir(parents=True,exist_ok=True)
 temp=p.with_name(p.name+'.ledger-upload');shutil.copy2(base/'workspace'/m['source'],temp);temp.replace(p)
'''
print(run('python3 -',code),flush=True)
rollback='''
import hashlib,json,pathlib,shutil,subprocess,time,urllib.request
base=pathlib.Path(__file__).resolve().parent
manifest=json.loads((base/'manifest.json').read_text())
for item in manifest:
 p=pathlib.Path(item['target'])
 if p.exists():assert hashlib.sha256(p.read_bytes()).hexdigest()==item['after'], 'Later changes detected: '+str(p)
api="""
import json,time,urllib.request,jwt
c=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'));d=c['dashboard']
t=jwt.encode({'username':d['username'],'exp':int(time.time())+120},d['jwt_secret'],algorithm='HS256')
h={'Authorization':'Bearer '+t}
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/get',headers=h)
names={p['name'] for p in json.load(urllib.request.urlopen(r,timeout=15))['data']}
if 'astrbot_plugin_request_ledger' in names:
 h['Content-Type']='application/json'
 r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/uninstall',data=json.dumps({'name':'astrbot_plugin_request_ledger','delete_data':False,'delete_config':False}).encode(),headers=h)
 v=json.load(urllib.request.urlopen(r,timeout=45));assert v.get('status')=='ok'
"""
subprocess.run(['docker','exec','-i','astrbot','python','-'],input=api.encode(),check=True,timeout=60)
for item in manifest:
 p=pathlib.Path(item['target']);old=base/'original'/p.relative_to('/')
 if item['before'] is not None:shutil.copy2(old,p)
 elif p.exists():p.unlink()
subprocess.run(['docker','restart','astrbot'],check=True,timeout=90)
print('Code restored; request ledger and official imports retained; game services unchanged.')
'''
with sftp.open(backup+'/rollback.py','w') as f:f.write(rollback)
# Import before activating capture so the baseline is explicit and race-free.
container_export='/AstrBot/data/temp/official-ledger-'+stamp+'.zip'
run('docker cp '+shlex.quote(backup+'/official-private.zip')+' astrbot:'+container_export)
importcode=f'''
from dafeiyu_runtime.request_ledger import Ledger
import json,pathlib
p=pathlib.Path({container_export!r})
try:
 result=Ledger().import_zip(p);assert result['days']['2026-10-29']['cost_nano']==1261369000
 print('Official import:',json.dumps(result['days']))
finally:p.unlink(missing_ok=True)
'''
print(run('docker exec -i astrbot python -',importcode),flush=True)
# Install a NEW plugin via the scoped upload endpoint. Passing an unknown name
# to reload() would reload every plugin in this framework version.
run('docker cp '+shlex.quote(backup+'/request-ledger-plugin.zip')+' astrbot:/tmp/request-ledger-plugin.zip')
api='''
import json,time,urllib.request,jwt,pathlib,uuid
cfg=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'));d=cfg['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
boundary=uuid.uuid4().hex
nl=chr(13)+chr(10)
head='--'+boundary+nl+'Content-Disposition: form-data; name="file"; filename="astrbot_plugin_request_ledger.zip"'+nl+'Content-Type: application/zip'+nl+nl
body=head.encode()+pathlib.Path('/tmp/request-ledger-plugin.zip').read_bytes()+(nl+'--'+boundary+'--'+nl).encode()
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/install-upload',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
result=json.load(urllib.request.urlopen(r,timeout=120));print('Install status:',result.get('status'));assert result.get('status')=='ok',result.get('message')
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':'astrbot_plugin_usage_view'}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
result=json.load(urllib.request.urlopen(r,timeout=45));print('Usage reload:',result.get('status'));assert result.get('status')=='ok'
'''
print(run('docker exec -i astrbot python -',api,timeout=180),flush=True)
after=run('systemctl show stronghold stronghold-3001 -p MainPID -p ActiveState')
assert before==after,'Game processes changed'
record={'backup':backup,'files':manifest,'plugin_installed':True,'container_restarted':False,'game_pids_unchanged':True}
(REVIEW/'deployment.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print('Installed without container/game restart. Backup:',backup,flush=True)
c.close()
