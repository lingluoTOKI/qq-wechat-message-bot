"""把线上 group_guard 的 notice_peak 从单条字符串替换为多样文案数组（带备份，幂等）。

只改 notice_peak 一个字段，其它字段原样保留；config 是每请求热读，改完即生效，无需重启。
"""
import json
import time
from pathlib import Path

import paramiko

ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".git").exists())
HOST = "101.132.104.41"
CONFIG = "/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_dafeiyu_group_guard/config.json"

NOTICE_PEAK = [
    "嗝——现在是高峰段，说句话都要双倍的钱，我先眯会儿，等 {end} 就醒，有事把话放这儿。",
    "困……又到这个贵死人的点了，本鲸先潜水，{end} 再冒泡，回见。",
    "这个点开口就是给平台白打工，溜了溜了，{end} 我再来。",
    "（打了个哈欠）高峰段烧钱，我待机到 {end}，真有事别只喊我一声啊。",
    "嘶——双倍价格太肉疼，我先装死到 {end}，不会真不回来的。",
]

notice_json = json.dumps(NOTICE_PEAK, ensure_ascii=False)

remote_code = f"""
import json, time, pathlib, shutil, os
cfg_path = pathlib.Path({CONFIG!r})
notice = json.loads({notice_json!r})
raw = cfg_path.read_text(encoding='utf-8-sig')
cfg = json.loads(raw)
cur = cfg.get('notice_peak')
if isinstance(cur, list) and cur == notice:
    print('SKIP already-array-and-identical')
else:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = pathlib.Path('/opt/bot-stack/backups') / f'group-guard-config-{{stamp}}.json'
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, backup)
    before_key = 'list(%d)' % len(cur) if isinstance(cur, list) else 'str'
    cfg['notice_peak'] = notice
    tmp = cfg_path.with_name(cfg_path.name + '.new')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, cfg_path)
    print(f'UPDATED before={{before_key}} backup={{backup}}')
check = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
assert isinstance(check['notice_peak'], list), 'notice_peak is not a list after write'
print('VERIFY ok items=%d' % len(check['notice_peak']))
"""


def main():
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", key_filename=str(ROOT / ".robot-nopass.pem"), timeout=15)
    try:
        stdin, stdout, stderr = client.exec_command("python3 -", timeout=60)
        stdin.write(remote_code)
        stdin.channel.shutdown_write()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        if stdout.channel.recv_exit_status():
            raise RuntimeError(out + err)
        print(out, flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()