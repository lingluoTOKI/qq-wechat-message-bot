"""Scoped deployment. Run after acceptance; never restart bot or game services."""
import json
from pathlib import Path
import paramiko

ROOT=Path(__file__).resolve().parents[2]
FILES={
    'plugins/dafeiyu_runtime/request_capture.py':'pylibs/dafeiyu_runtime/request_capture.py',
    'plugins/dafeiyu_runtime/request_ledger.py':'pylibs/dafeiyu_runtime/request_ledger.py',
    'plugins/dafeiyu_runtime/zhipu_attribution.py':'pylibs/dafeiyu_runtime/zhipu_attribution.py',
    'plugins/astrbot_plugin_dafeiyu_daily_memory/llm_client.py':'plugins/astrbot_plugin_dafeiyu_daily_memory/llm_client.py',
    'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_style.py':'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_style.py',
    'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py':'plugins/astrbot_plugin_dafeiyu_daily_memory/birthday_watch.py',
    'plugins/patches/plugin_optimization_20261029/astrbot_plugin_livingmemory/core/processors/memory_processor.py':'plugins/astrbot_plugin_livingmemory/core/processors/memory_processor.py',
}


def main():
    c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    try:
        with c.open_sftp() as s:
            for i,source in enumerate(FILES):s.put(str(ROOT/source),f'/opt/bot-stack/astrbot/data/temp/glm-aux-source-{i}')
        code=r'''import json,time,shutil,hashlib,jwt,httpx,sys
from pathlib import Path
root=Path('/AstrBot/data');backup=Path((root/'temp/glm-aux-backup-path.txt').read_text())
targets=json.loads(TARGETS)
cfg=json.loads((root/'cmd_config.json').read_text(encoding='utf-8-sig'));d=cfg['dashboard'];headers={'Authorization':'Bearer '+jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')};base='http://127.0.0.1:6185'
def api(path,data):
    r=httpx.post(base+path,headers=headers,json=data,timeout=60);x=r.json()
    if r.status_code!=200 or x.get('status')!='ok':raise RuntimeError(path+': '+str(x.get('message')))
    return x
items=httpx.get(base+'/api/plugin/get',headers=headers).json()['data'];items=items if isinstance(items,list) else items.get('plugins',[])
names={p['name'] for p in items}
for name in ['astrbot_plugin_request_ledger','astrbot_plugin_dafeiyu_daily_memory','astrbot_plugin_livingmemory']:assert name in names
before_guard=(root/'plugin_data/astrbot_plugin_dafeiyu_group_guard/config.json').read_bytes()
preserved={name:(root/'config'/name).read_bytes() for name in ['meme_manager_config.json','spectrecore_config.json']}
changes=[]
for i,target in enumerate(targets):
    p=root/target;b=backup/target;b.parent.mkdir(parents=True,exist_ok=True)
    existed=p.exists()
    if existed:shutil.copy(p,b)
    shutil.copy(root/'temp'/('glm-aux-source-'+str(i)),p)
    changes.append({'target':str(p),'existed':existed,'backup':str(b) if existed else None,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
api('/api/plugin/reload',{'name':'astrbot_plugin_request_ledger'})
configs=[]
for name,keys in [('astrbot_plugin_dafeiyu_daily_memory',['extract_provider_id','birthday_provider_id']),('astrbot_plugin_livingmemory',[])]:
    path=root/'config'/(name+'_config.json');shutil.copy(path,backup/path.name);data=json.loads(path.read_text(encoding='utf-8-sig'))
    if keys:
        for k in keys:data[k]='zhipu/glm-5.3-flashx'
    else:data['provider_settings']['llm_provider_id']='zhipu/glm-5.3-flashx'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2));api('/api/plugin/reload',{'name':name});configs.append(name)
sys.path.insert(0,'/AstrBot/data/temp');from context_acceptance import CUSTOM
cfg=json.loads((root/'cmd_config.json').read_text(encoding='utf-8-sig'));old_compression=dict(cfg['agent_runner']['config']['compression'])
assert cfg['agent_runner']['config']['model']['provider_id']=='deepseek/deepseek-flash'
cfg['agent_runner']['config']['compression']['provider_id']='zhipu/glm-5.3-flashx'
cfg['agent_runner']['config']['compression']['instruction']=CUSTOM
api('/api/config/astrbot/update',{'conf_id':'default','config':cfg})
after=json.loads((root/'cmd_config.json').read_text(encoding='utf-8-sig'))
assert after['agent_runner']['config']['compression']['provider_id']=='zhipu/glm-5.3-flashx'
assert after['agent_runner']['config']['model']['provider_id']=='deepseek/deepseek-flash'
assert all((root/'config'/n).read_bytes()==v for n,v in preserved.items())
assert (root/'plugin_data/astrbot_plugin_dafeiyu_group_guard/config.json').read_bytes()==before_guard
result={'backup':str(backup),'files':changes,'plugin_configs':configs,'compression_before':old_compression,'compression_provider':'zhipu/glm-5.3-flashx','main_chat_unchanged':True,'vision_and_meme_unchanged':True,'daily_budget_unchanged':True}
(root/'temp/glm-aux-deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
'''.replace('TARGETS',repr(json.dumps(list(FILES.values()))))
        i,o,e=c.exec_command('docker exec -i astrbot python -',timeout=220);i.write(code);i.flush();i.channel.shutdown_write()
        out=o.read().decode()+e.read().decode();(ROOT/'review/glm-aux-deployment.log').write_text(out,encoding='utf-8');print(out[-6000:])
        assert o.channel.recv_exit_status()==0
        code="""from pathlib import Path
import json,shutil
p=Path('/opt/bot-stack/astrbot/data/temp/glm-aux-deployment.json');x=json.loads(p.read_text());source=Path(x['backup'].replace('/AstrBot/data','/opt/bot-stack/astrbot/data'));target=Path('/opt/bot-stack/backups')/source.name;shutil.copytree(source,target);x['durable_backup']=str(target);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
"""
        i,o,e=c.exec_command('python3 -',timeout=30);i.write(code);i.flush();i.channel.shutdown_write();o.read();assert o.channel.recv_exit_status()==0,e.read().decode()
        with c.open_sftp() as s:s.get('/opt/bot-stack/astrbot/data/temp/glm-aux-deployment.json',str(ROOT/'review/glm-aux-deployment.json'))
    finally:c.close()


if __name__=='__main__':main()
