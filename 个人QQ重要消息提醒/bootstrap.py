"""Prepare encrypted credentials and account-specific loopback OneBot config."""
import json
import secrets
from pathlib import Path

import paramiko

from vault import seal

root=Path(__file__).parent
client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('101.132.104.41',username='root',key_filename=str(root.parent/'.robot-nopass.pem'),timeout=12)
try:
    sftp=client.open_sftp()
    with sftp.open('/opt/bot-stack/astrbot/data/cmd_config.json','rb') as handle:
        config=json.loads(handle.read().decode('utf-8-sig'))
finally:
    client.close()
source=next(p for p in config['provider_sources'] if p['id']=='deepseek')
keys=source['key'];key=next(k for k in keys if k) if isinstance(keys,list) else keys
if not isinstance(key,str) or not key:raise RuntimeError('No configured API key')
vault=root/'secrets.dpapi'
if vault.exists():raise RuntimeError('Credentials already prepared; refusing to overwrite')
token=secrets.token_urlsafe(32)
vault.write_text(seal(json.dumps({'api_key':key,'onebot_token':token,'callback_token':secrets.token_urlsafe(32)})),encoding='utf-8')
runtime=root.parent/'snowluma相关'/'runtime-v1.14.22'
target=runtime/'config'/'onebot_483257228.json'
if target.exists():raise RuntimeError('Account configuration already exists; manual merge required')
onebot={'mode':'snapshot','networks':{'httpServers':[],'httpClients':[],'wsServers':[{'name':'personal-notifier','host':'127.0.0.1','port':31997,'path':'/','role':'Universal','accessToken':token,'messageFormat':'array','reportSelfMessage':False}],'wsClients':[]},'statusCommand':{'enabled':False,'swallow':False,'cooldownSeconds':5,'trigger':'#sl'},'historySync':{'enabled':False},'notifications':{'channelIds':[]}}
target.write_text(json.dumps(onebot,ensure_ascii=False,indent=2),encoding='utf-8')
print('Prepared: encrypted credentials and loopback-only configuration for QQ 483257228. No messages read, no API/model calls made.')
