import json, os, shutil
from datetime import datetime
P = "/opt/bot-stack/astrbot/data/config/astrbot_plugin_dafeiyu_cover_config.json"
tok = open("/opt/bot-stack/bin/local_tts_token.txt").read().strip()
d = {"enabled": True,
     "proxy_url": "http://172.18.0.1:32000",
     "proxy_token": tok,
     "max_seconds": 60,
     "timeout": 320,
     "wait_ttl": 180}
if os.path.exists(P):
    shutil.copy(P, P + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
chk = json.load(open(P, encoding="utf-8"))
print("配置已写入:")
for k, v in chk.items():
    print("  %-14s = %s" % (k, v if k != "proxy_token" else "%d 字符" % len(v)))
assert chk["proxy_token"] == tok
print("token 与服务器一致: OK")
