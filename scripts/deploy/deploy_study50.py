"""Install the new study plugin after isolated real-framework checks."""
import hashlib
import json
import tarfile
import time
import zipfile
from pathlib import Path
import paramiko

ROOT=Path(__file__).resolve().parents[1]
NAME='astrbot_plugin_xiaoluo_study50'


def main():
    plugin=ROOT/'plugins'/NAME
    archive=ROOT/'review'/f'{NAME}-1.0.0.zip'
    files=[p for p in plugin.iterdir() if p.is_file() and p.suffix in ('.py','.json','.yaml','.md','.txt')]
    for p in files:
        if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,p.name)
    stage_archive=ROOT/'review/study50-tests.tar.gz'
    with tarfile.open(stage_archive,'w:gz') as tar:
        for p in files:tar.add(p,arcname=f'plugins/{NAME}/'+p.name)
        for p in (ROOT/'tests/study50').glob('*.py'):tar.add(p,arcname='tests/study50/'+p.name)
    client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        inp,out,err=client.exec_command('docker exec -i astrbot python -',timeout=180)
        inp.write(code);inp.channel.shutdown_write()
        output,error=out.read().decode('utf-8'),err.read().decode('utf-8')
        if out.channel.recv_exit_status():raise RuntimeError(output+error)
        print(output,flush=True)
    try:
        with client.open_sftp() as sftp:
            try:sftp.stat('/opt/bot-stack/astrbot/data/plugins/'+NAME)
            except FileNotFoundError:pass
            else:raise RuntimeError('Plugin already exists; review its deployed state instead of reinstalling')
            sftp.put(str(stage_archive),'/opt/bot-stack/astrbot/data/temp/study50-tests.tar.gz')
            sftp.put(str(archive),'/opt/bot-stack/astrbot/data/temp/'+NAME+'.zip')
        run('''import asyncio,importlib,os,pathlib,subprocess,sys,tarfile,tempfile
from types import SimpleNamespace
from unittest.mock import patch
stage=pathlib.Path('/tmp/study50-acceptance');stage.mkdir(exist_ok=True)
with tarfile.open('/AstrBot/data/temp/study50-tests.tar.gz') as tar:tar.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/study50','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/study50-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-400:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'))
m=importlib.import_module('astrbot_plugin_xiaoluo_study50.main')
async def check():
 with tempfile.TemporaryDirectory() as tmp:
  with patch.object(m.StarTools,'get_data_dir',return_value=pathlib.Path(tmp)),patch.dict(os.environ,{'NOTION_STUDY_TOKEN':''}):
   obj=m.Study50(SimpleNamespace(),{})
   event=SimpleNamespace(get_sender_id=lambda:'test-admin',is_admin=lambda:True,
     unified_msg_origin='isolated:FriendMessage:test',message_str='/考研 D01',stop_event=lambda:None,plain_result=lambda text:text)
   outputs=[x async for x in obj.command(event)];assert len(outputs)==1 and '极限' in outputs[0]
   event.is_admin=lambda:False
   denied=[x async for x in obj.command(event)];assert '授权' in denied[0]
   await obj.initialize();task=obj.task;await asyncio.sleep(.01);await obj.terminate();assert task.done()
   assert obj.store.setting('reminder_enabled')!='1' and not obj.sync.client.token
 print('actual framework: import, command, authorization, default-off reminders and lifecycle verified')
asyncio.run(check())
''')
        run('''import json,pathlib,time,urllib.request,uuid,jwt
cfg=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'));d=cfg['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
boundary=uuid.uuid4().hex;nl=chr(13)+chr(10)
head='--'+boundary+nl+'Content-Disposition: form-data; name="file"; filename="astrbot_plugin_xiaoluo_study50.zip"'+nl+'Content-Type: application/zip'+nl+nl
body=head.encode()+pathlib.Path('/AstrBot/data/temp/astrbot_plugin_xiaoluo_study50.zip').read_bytes()+(nl+'--'+boundary+'--'+nl).encode()
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/install-upload',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
x=json.load(urllib.request.urlopen(r,timeout=120));assert x.get('status')=='ok';print('study50 install ok')
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name=astrbot_plugin_xiaoluo_study50',headers={'Authorization':'Bearer '+token})
x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated'];assert x['version']=='1.0.0';print('study50 activated; components:',len(x['components']))
''')
        manifest={'plugin':NAME,'version':'1.0.0','installed_at':time.time(),'files':[],'reminders_enabled':False,'notion_runtime_authorization_pending':True}
        with client.open_sftp() as sftp:
            for p in files:
                target='/opt/bot-stack/astrbot/data/plugins/'+NAME+'/'+p.name
                live=sftp.open(target,'rb').read();sha=hashlib.sha256(p.read_bytes()).hexdigest()
                assert hashlib.sha256(live).hexdigest()==sha,p.name
                manifest['files'].append({'target':target,'sha256':sha})
            sftp.get('/opt/bot-stack/astrbot/data/temp/study50-tests.log',str(ROOT/'review/study50-tests.log'))
        (ROOT/'review/study50-deployment.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print('Deployed files verified:',len(files))
    finally:client.close()


if __name__=='__main__':main()
