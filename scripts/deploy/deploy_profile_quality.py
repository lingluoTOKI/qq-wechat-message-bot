"""Rehearse, back up and apply evidence-based profile cleanup without model calls."""
import hashlib
import importlib.util
import json
import tarfile
import time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
BASE='/opt/bot-stack/astrbot/data/'
FILES={
 'plugins/dafeiyu_runtime/profile_quality.py':'pylibs/dafeiyu_runtime/profile_quality.py',
 'plugins/dafeiyu_runtime/memory.py':'pylibs/dafeiyu_runtime/memory.py',
 'plugins/astrbot_plugin_request_ledger/main.py':'plugins/astrbot_plugin_request_ledger/main.py',
 **{'plugins/astrbot_plugin_dafeiyu_daily_memory/'+name:'plugins/astrbot_plugin_dafeiyu_daily_memory/'+name
    for name in ['main.py','extract.py','store.py','recall.py','metadata.yaml']}}


def main():
    baseline=json.loads((ROOT/'review/final-source-baseline.json').read_text())
    spec=importlib.util.spec_from_file_location('manifest_registry',ROOT/'scripts/tests/verify_acceptance_deployment.py')
    registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)
    for path in registry.MANIFESTS:
        for row in json.loads(path.read_text())['files']:baseline[row['target']]=row['after']
    archive=ROOT/'review/profile-quality-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name in ['vendor/astrbot/astrbot_plugin_lol_esports/main.py','scripts/diagnostics/bot_event_audit.py']:
            tar.add(ROOT/name,arcname=name)
        for folder in ['plugins','tests/plugin_optimization']:
            for p in (ROOT/folder).rglob('*'):
                if p.is_file() and p.suffix in ('.py','.json','.yaml','.txt'):
                    if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
                    tar.add(p,arcname=p.relative_to(ROOT))
    client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        inp,out,err=client.exec_command('docker exec -i astrbot python -',timeout=180)
        inp.write(code);inp.channel.shutdown_write();output,error=out.read().decode(),err.read().decode()
        if out.channel.recv_exit_status():raise RuntimeError(output+error)
        print(output,flush=True)
    try:
        with client.open_sftp() as s:s.put(str(archive),BASE+'temp/profile-quality-tests.tar.gz')
        run('''import importlib,pathlib,subprocess,sys,tarfile,sqlite3,json,tempfile
stage=pathlib.Path('/tmp/profile-quality-acceptance');stage.mkdir(exist_ok=True)
with tarfile.open('/AstrBot/data/temp/profile-quality-tests.tar.gz') as tar:tar.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/profile-quality-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-1000:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'))
from dafeiyu_runtime import profile_quality as q
from astrbot_plugin_dafeiyu_daily_memory.store import Store
importlib.import_module('astrbot_plugin_dafeiyu_daily_memory.main')
src=sqlite3.connect('file:/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db?mode=ro',uri=True)
with tempfile.TemporaryDirectory() as temp:
 path=pathlib.Path(temp)/'rehearsal.db';dst=sqlite3.connect(path);src.backup(dst);dst.close();src.close()
 store=Store(str(path))
 def counts():return [store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['events','facts','people']]
 before=counts();birthdays=[tuple(r) for r in store.db.execute('SELECT sender_id,birthday FROM people ORDER BY sender_id')]
 report=q.repair(store,apply=True)
 assert counts()==before
 assert birthdays==[tuple(r) for r in store.db.execute('SELECT sender_id,birthday FROM people ORDER BY sender_id')]
 assert q.repair(store,apply=True)['summaries_cleaned']==0
 store.close()
 print('Real-data copy rehearsal:',json.dumps(report,ensure_ascii=False))
pathlib.Path('/AstrBot/data/temp/profile-quality-rehearsal.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
''')
        backup='/opt/bot-stack/backups/profile-quality-'+time.strftime('%Y%m%d-%H%M%S')
        manifest={'backup':backup,'files':[],'raw_chat_deleted':0,'forced_paid_rebuild':False}
        with client.open_sftp() as s:
            s.mkdir(backup,mode=0o700)
            for i,(source,dest) in enumerate(FILES.items()):
                target=BASE+dest
                try:
                    with s.open(target,'rb') as f:before=f.read()
                except FileNotFoundError:before=None
                sha=hashlib.sha256(before).hexdigest() if before is not None else None
                assert sha==baseline.get(target),'Live source changed: '+target
                data=(ROOT/source).read_bytes()
                row={'target':target,'before':sha,'after':hashlib.sha256(data).hexdigest(),'backup':backup+f'/{i}.before'}
                if before is not None:
                    with s.open(row['backup'],'wb') as f:f.write(before)
                    s.chmod(row['backup'],0o600)
                manifest['files'].append(row)
        run('''import sqlite3,os
for name,path in [('profiles','/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db'),('semantic','/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db')]:
 src=sqlite3.connect('file:'+path+'?mode=ro',uri=True);dest='/AstrBot/data/temp/profile-quality-'+name+'-before.db'
 dst=sqlite3.connect(dest);src.backup(dst);dst.close();src.close();os.chmod(dest,0o600)
print('Consistent profile and semantic database snapshots created')
''')
        with client.open_sftp() as s:
            for name in ['profiles','semantic']:
                s.rename(BASE+'temp/profile-quality-'+name+'-before.db',backup+'/'+name+'-before.db')
            for source,dest in FILES.items():
                target=BASE+dest
                s.put(str(ROOT/source),target+'.quality-new');s.posix_rename(target+'.quality-new',target)
            for row in manifest['files']:
                with s.open(row['target'],'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==row['after']
            for name in ['tests.log','rehearsal.json']:
                s.get(BASE+'temp/profile-quality-'+name,str(ROOT/'review'/('profile-quality-'+name)))
        (ROOT/'review/profile-quality-deployment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        run('''import sys,json,pathlib,sqlite3,time
sys.path.insert(0,'/AstrBot/data/plugins');sys.path.insert(0,'/AstrBot/data/pylibs')
from astrbot_plugin_dafeiyu_daily_memory.store import Store
from dafeiyu_runtime import profile_quality as q
store=Store('/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db')
before_events=store.db.execute('SELECT count(*) FROM events').fetchone()[0]
before_facts=store.db.execute('SELECT count(*) FROM facts').fetchone()[0]
before_birthdays=[tuple(r) for r in store.db.execute('SELECT sender_id,birthday FROM people ORDER BY sender_id')]
old_quality={r[0] for r in store.db.execute('SELECT fact_id FROM fact_quality')}
before_revision=store.db.execute('SELECT coalesce(max(id),0) FROM profile_revisions').fetchone()[0]
report=q.repair(store,apply=True)
assert store.db.execute('SELECT count(*) FROM events').fetchone()[0]>=before_events
assert store.db.execute('SELECT count(*) FROM facts').fetchone()[0]>=before_facts
assert before_birthdays==[tuple(r) for r in store.db.execute('SELECT sender_id,birthday FROM people ORDER BY sender_id')]
report['new_quality_ids']=[r[0] for r in store.db.execute('SELECT fact_id FROM fact_quality') if r[0] not in old_quality]
report['new_revision_ids']=[r[0] for r in store.db.execute('SELECT id FROM profile_revisions WHERE id>?',(before_revision,))]
report['events_preserved']=before_events;report['fact_rows_preserved']=before_facts
report['people_total']=store.db.execute('SELECT count(*) FROM people').fetchone()[0]
store.close()
pathlib.Path('/AstrBot/data/temp/profile-quality-applied.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('Production cleanup:',json.dumps(report,ensure_ascii=False))
''')
        run('''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
for name in ['astrbot_plugin_dafeiyu_daily_memory','astrbot_plugin_request_ledger']:
 r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':name}).encode(),headers=headers)
 x=json.load(urllib.request.urlopen(r,timeout=90));assert x.get('status')=='ok',name
 r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name='+name,headers=headers)
 x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated']
 print('Reloaded:',name,'version:',x['version'])
''')
        with client.open_sftp() as s:s.get(BASE+'temp/profile-quality-applied.json',str(ROOT/'review/profile-quality-applied.json'))
        print('Verified source files:',len(manifest['files']),'Backup:',backup)
    finally:client.close()


if __name__=='__main__':main()
