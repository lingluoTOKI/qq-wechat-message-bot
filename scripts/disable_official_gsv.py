"""停用官方 GPT_SoVITS 插件（避免与我们新加的 /说 命令冲突）。

只改 data_v4.db 的 preferences.inactivated_plugins（面板开关用的就是这里），
不删目录、不删配置 —— 用户在面板里随时能再打开或彻底卸载。
"""
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB = "/opt/bot-stack/astrbot/data/data_v4.db"
TARGET = "astrbot_plugin_GPT_SoVITS"

bak = "%s.bak-disable-gsv-%s" % (DB, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(DB, bak)
print("已备份:", Path(bak).name)

c = sqlite3.connect(DB, timeout=15)
row = c.execute("select value from preferences where key='inactivated_plugins'").fetchone()
raw = row[0] if row else '{"val": []}'
try:
    data = json.loads(raw)
except Exception:
    data = {"val": []}
if isinstance(data, dict):
    lst = data.get("val") or []
else:
    lst = data if isinstance(data, list) else []
    data = {"val": lst}

before = list(lst)
if TARGET not in lst:
    lst.append(TARGET)
data["val"] = lst
c.execute(
    "update preferences set value=? where key='inactivated_plugins'",
    (json.dumps(data, ensure_ascii=False),),
)
if c.total_changes == 0:
    c.execute(
        "insert into preferences(key,value) values('inactivated_plugins',?)",
        (json.dumps(data, ensure_ascii=False),),
    )
c.commit()

chk = json.loads(
    c.execute("select value from preferences where key='inactivated_plugins'").fetchone()[0]
)["val"]
c.close()

print("改前:", before)
print("改后:", chk)
assert TARGET in chk, "写入失败"
print("校验通过：%s 已加入停用列表（重启后生效）" % TARGET)
