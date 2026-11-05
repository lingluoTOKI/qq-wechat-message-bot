"""把线上 parser 的 source_max_size 改成指定 MB 并 reload 插件（带备份）。

用法：python deploy_parser_max_size.py [MB]，默认 100。
"""
import sys
import time
from pathlib import Path

import paramiko

ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".git").exists())
HOST = "101.132.104.41"
TARGET = "/opt/bot-stack/astrbot/data/config/astrbot_plugin_parser_config.json"
SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 100

REMOTE = f"""
import json, time, pathlib, shutil, os
cfg_path = pathlib.Path({TARGET!r})
cfg = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
old = cfg.get('source_max_size')
if old == {SIZE}:
    print('SKIP already-{SIZE}')
else:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = pathlib.Path('/opt/bot-stack/backups') / f'parser-config-{{stamp}}.json'
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, backup)
    cfg['source_max_size'] = {SIZE}
    tmp = cfg_path.with_name(cfg_path.name + '.new')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, cfg_path)
    print(f'UPDATED source_max_size {{old}} -> {SIZE} backup={{backup}}')
check = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
assert check['source_max_size'] == {SIZE}, 'write failed'
print('VERIFY ok max_size={SIZE} max_minute=%s' % check.get('source_max_minute'))
"""

RELOAD = """
import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+300},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
name='astrbot_plugin_parser'
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':name}).encode(),headers=headers)
x=json.load(urllib.request.urlopen(r,timeout=90));assert x.get('status')=='ok',x
print('Reloaded:',name)
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name='+name,headers=headers)
assert json.load(urllib.request.urlopen(r,timeout=30))['data']['activated'],name
print('Activated:',name)
"""

def main():
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", key_filename=str(ROOT / ".robot-nopass.pem"), timeout=15)
    try:
        i, o, e = c.exec_command("python3 -", timeout=60)
        i.write(REMOTE)
        i.channel.shutdown_write()
        out = o.read().decode(errors="replace")
        if o.channel.recv_exit_status():
            raise RuntimeError(out + e.read().decode(errors="replace"))
        print(out, flush=True)

        i, o, e = c.exec_command("docker exec -i astrbot python -", timeout=180)
        i.write(RELOAD)
        i.channel.shutdown_write()
        out = o.read().decode(errors="replace")
        if o.channel.recv_exit_status():
            raise RuntimeError(out + e.read().decode(errors="replace"))
        print(out, flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()