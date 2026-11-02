"""部署 group_guard 高峰拒绝列表代码 + proactive_chat 高峰不主动说话代码（带备份）。

线上 config.json 的 notice_peak 已改成数组，但线上 main.py 还是旧版 cfg["notice_peak"].format(...)，
配数组会崩，必须立即把新代码上线并 reload 插件。
"""
import hashlib
import time
from pathlib import Path

import paramiko

ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".git").exists())
HOST = "101.132.104.41"
BASE = "/opt/bot-stack/astrbot/data"

FILES = {
    "plugins/astrbot_plugin_dafeiyu_group_guard/main.py":
        BASE + "/plugins/astrbot_plugin_dafeiyu_group_guard/main.py",
    "plugins/patches/plugin_optimization_20261029/astrbot_plugin_proactive_chat/core/chat_flow.py":
        BASE + "/plugins/astrbot_plugin_proactive_chat/core/chat_flow.py",
}

RELOAD_CODE = """
import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+300},d['jwt_secret'],algorithm='HS256')
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
for name in ['astrbot_plugin_dafeiyu_group_guard','astrbot_plugin_proactive_chat']:
    r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':name}).encode(),headers=headers)
    x=json.load(urllib.request.urlopen(r,timeout=90));assert x.get('status')=='ok',name
    print('Reloaded:',name)
    r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name='+name,headers=headers)
    assert json.load(urllib.request.urlopen(r,timeout=30))['data']['activated'],name
    print('Activated:',name)
"""


def sh(c, cmd, timeout=60):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    if o.channel.recv_exit_status():
        raise RuntimeError(out + err)
    return out


def main():
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", key_filename=str(ROOT / ".robot-nopass.pem"), timeout=15)
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"/opt/bot-stack/backups/peak-code-{stamp}"
        sh(c, f"mkdir -p {backup}")
        sftp = c.open_sftp()
        for src_rel, target in FILES.items():
            src = ROOT / src_rel
            data = src.read_bytes()
            # 备份原文件
            sh(c, f"cp -a {target} {backup}/")
            # 上传临时 + 原子替换
            tmp = target + ".new"
            with sftp.open(tmp, "wb") as f:
                f.write(data)
            sh(c, f"mv -f {tmp} {target}")
            # 校验：读回字节比对
            with sftp.open(target, "rb") as f:
                live = f.read()
            assert hashlib.sha256(live).hexdigest() == hashlib.sha256(data).hexdigest(), src_rel
            print("Installed:", src_rel, "backup:", backup)
        sftp.close()
        # reload 两个插件
        i, o, e = c.exec_command("docker exec -i astrbot python -", timeout=180)
        i.write(RELOAD_CODE)
        i.channel.shutdown_write()
        out = o.read().decode(errors="replace")
        err = e.read().decode(errors="replace")
        if o.channel.recv_exit_status():
            raise RuntimeError(out + err)
        print(out, flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()