"""Test, back up and reload only the personalized birthday plugin."""
import hashlib
import json
import tarfile
import time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
NAME='astrbot_plugin_dafeiyu_daily_memory'
SOURCE=ROOT/'plugins'/NAME
if not SOURCE.exists():SOURCE=ROOT/'plugins'/NAME
NAMES=['main.py','birthday_watch.py','birthday_style.py','affinity.py','store.py','metadata.yaml']


def main():
    baseline=json.loads((ROOT/'review/final-source-baseline.json').read_text(encoding='utf-8'))
    for name in ['birthday-recovery','birthday-input']:
        for row in json.loads((ROOT/'review'/f'{name}-deployment.json').read_text(encoding='utf-8'))['files']:
            baseline[row['target']]=row['after']
    previous_path=ROOT/'review/birthday-style-deployment.json'
    previous=json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else None
    if previous:
        for row in previous['files']:baseline[row['target']]=row['after']
    archive=ROOT/'review/birthday-style-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for p in SOURCE.iterdir():
            if p.is_file():
                if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
                tar.add(p,arcname=f'plugins/{NAME}/'+p.name)
        for p in (ROOT/'tests/plugin_optimization').glob('*.py'):
            tar.add(p,arcname='tests/plugin_optimization/'+p.name)
    client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        inp,out,err=client.exec_command('docker exec -i astrbot python -',timeout=180)
        inp.write(code);inp.channel.shutdown_write()
        output,error=out.read().decode('utf-8'),err.read().decode('utf-8')
        if out.channel.recv_exit_status():raise RuntimeError(output+error)
        print(output,flush=True)
    try:
        with client.open_sftp() as sftp:sftp.put(str(archive),'/opt/bot-stack/astrbot/data/temp/birthday-style-tests.tar.gz')
        run('''import pathlib,subprocess,tarfile
stage=pathlib.Path('/tmp/acceptance-final-20261029')
with tarfile.open('/AstrBot/data/temp/birthday-style-tests.tar.gz') as tar:tar.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/birthday-style-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-1500:]);assert r.returncode==0
''')
        backup='/opt/bot-stack/backups/birthday-style-'+time.strftime('%Y%m%d-%H%M%S')
        manifest={'backup':backup,'files':[]}
        with client.open_sftp() as sftp:
            sftp.mkdir(backup,mode=0o700)
            # Validate the entire batch before altering any live source.
            for name in NAMES:
                target='/opt/bot-stack/astrbot/data/plugins/'+NAME+'/'+name
                try:
                    with sftp.open(target,'rb') as stream:before=stream.read()
                except FileNotFoundError:before=None
                sha=hashlib.sha256(before).hexdigest() if before is not None else None
                assert sha==baseline.get(target), 'Live source changed: '+target
                row={'target':target,'before':sha,'after':hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),
                     'backup':backup+'/'+name+'.before'}
                if before is not None:
                    with sftp.open(row['backup'],'wb') as stream:stream.write(before)
                manifest['files'].append(row)
        run('''import os,sqlite3
src=sqlite3.connect('file:/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db?mode=ro',uri=True)
dst=sqlite3.connect('/AstrBot/data/temp/birthday-style-memory-before.sqlite3')
src.backup(dst);dst.close();src.close()
os.chmod('/AstrBot/data/temp/birthday-style-memory-before.sqlite3',0o600)
print('Consistent memory database backup created')
''')
        with client.open_sftp() as sftp:
            sftp.rename('/opt/bot-stack/astrbot/data/temp/birthday-style-memory-before.sqlite3',backup+'/memory-before.sqlite3')
            for row in manifest['files']:
                sftp.put(str(SOURCE/Path(row['target']).name),row['target'])
                with sftp.open(row['target'],'rb') as stream:assert hashlib.sha256(stream.read()).hexdigest()==row['after']
            sftp.get('/opt/bot-stack/astrbot/data/temp/birthday-style-tests.log',str(ROOT/'review/birthday-style-tests.log'))
        if previous:
            original={row['target']:row for row in previous['files']}
            for row in manifest['files']:
                old=original[row['target']]
                row['before']=old['before'];row['backup']=old['backup']
        (ROOT/'review/birthday-style-deployment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        run('''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':'astrbot_plugin_dafeiyu_daily_memory'}).encode(),headers=headers)
x=json.load(urllib.request.urlopen(r,timeout=90));assert x.get('status')=='ok';print('Memory plugin reload ok')
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name=astrbot_plugin_dafeiyu_daily_memory',headers=headers)
x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated'];assert x['version'].lstrip('v')=='0.3.4'
print('Active version:',x['version'])
''')
        print('Verified live source files:',len(manifest['files']),'Backup:',backup)
    finally:client.close()


if __name__=='__main__':main()
