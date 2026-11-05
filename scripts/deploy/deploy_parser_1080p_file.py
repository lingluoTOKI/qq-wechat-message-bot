"""B站 1080P + 视频以文件形式发送：上传 sender.py、改 video_quality 并 reload（带备份）。"""
import hashlib
import time
from pathlib import Path

import paramiko

ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".git").exists())
HOST = "101.132.104.41"
BASE = "/opt/bot-stack/astrbot/data"
LOCAL_SENDER = "plugins/patches/plugin_optimization_20261029/astrbot_plugin_parser/core/sender.py"
REMOTE_SENDER = BASE + "/plugins/astrbot_plugin_parser/core/sender.py"
CONFIG = BASE + "/config/astrbot_plugin_parser_config.json"

PATCH_QUALITY = f"""
import json, time, pathlib, shutil, os
cfg_path = pathlib.Path({CONFIG!r})
cfg = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
changed = None
for item in cfg.get('parsers_template', []):
    if item.get('__template_key') == 'bilibili':
        old = item.get('video_quality')
        item['video_quality'] = '_1080P'
        changed = old
stamp = time.strftime('%Y%m%d-%H%M%S')
backup = pathlib.Path('/opt/bot-stack/backups') / f'parser-config-{{stamp}}.json'
backup.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(cfg_path, backup)
tmp = cfg_path.with_name(cfg_path.name + '.new')
tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(tmp, cfg_path)
print(f'video_quality {{changed}} -> _1080P backup={{backup}}')
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


def run(c, cmd, code=None, timeout=120):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    if code is not None:
        i.write(code)
        i.channel.shutdown_write()
    out = o.read().decode(errors="replace")
    if o.channel.recv_exit_status():
        raise RuntimeError(out + e.read().decode(errors="replace"))
    return out


def main():
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", key_filename=str(ROOT / ".robot-nopass.pem"), timeout=15)
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"/opt/bot-stack/backups/parser-1080p-{stamp}"
        run(c, f"mkdir -p {backup} && cp -a {REMOTE_SENDER} {backup}/ && cp -a {CONFIG} {backup}/")

        data = (ROOT / LOCAL_SENDER).read_bytes()
        sftp = c.open_sftp()
        tmp = REMOTE_SENDER + ".new"
        with sftp.open(tmp, "wb") as f:
            f.write(data)
        sftp.close()
        run(c, f"mv -f {tmp} {REMOTE_SENDER}")

        sftp = c.open_sftp()
        with sftp.open(REMOTE_SENDER, "rb") as f:
            live = f.read()
        sftp.close()
        assert hashlib.sha256(live).hexdigest() == hashlib.sha256(data).hexdigest()
        print("Installed sender.py backup:", backup)

        print(run(c, "python3 -", PATCH_QUALITY).strip())
        print(run(c, "docker exec -i astrbot python -", RELOAD).strip(), flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()