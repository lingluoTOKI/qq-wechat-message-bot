import json
import urllib.request
import urllib.error

d = json.load(open("/opt/bot-stack/astrbot/data/cmd_config.json", encoding="utf-8"))
appid = secret = ""
for p in d.get("platform", []):
    if p.get("type") == "qq_official":
        appid, secret = p.get("appid", ""), p.get("secret", "")
        break
print("appid =", appid)

# 1) 换 token
req = urllib.request.Request(
    "https://bots.qq.com/app/getAppAccessToken",
    data=json.dumps({"appId": appid, "clientSecret": secret}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    token = j.get("access_token")
    print("换 token: OK, expires_in =", j.get("expires_in"))
except urllib.error.HTTPError as e:
    print("换 token 失败:", e.code, e.read().decode()[:300])
    raise SystemExit(1)

# 2) 试几种 C2C 路径
import sqlite3
c = sqlite3.connect("file:/opt/bot-stack/astrbot/data/plugin_data/wushu_ops/assistant.sqlite?mode=ro", uri=True)
row = c.execute("select v from state where k='admin'").fetchone()
c.close()
openid = row[0].split(":", 2)[2]
print("openid =", openid)

BODY = json.dumps({"content": "【自测】依赖体检告警链路测试，收到请忽略。", "msg_type": 0}).encode()
PATHS = [
    ("c2c v2 users", "https://api.sgroup.qq.com/v2/users/%s/messages" % openid),
    ("c2c v1 users", "https://api.sgroup.qq.com/users/%s/messages" % openid),
    ("group 风格", "https://api.sgroup.qq.com/v2/groups/%s/messages" % openid),
]
for name, url in PATHS:
    req = urllib.request.Request(
        url, data=BODY,
        headers={"Authorization": "QQBot %s" % token, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("%-16s -> HTTP %s  %s" % (name, r.status, r.read().decode()[:160]))
    except urllib.error.HTTPError as e:
        print("%-16s -> HTTP %s  %s" % (name, e.code, e.read().decode()[:220]))
    except Exception as e:
        print("%-16s -> ERR %s" % (name, e))
