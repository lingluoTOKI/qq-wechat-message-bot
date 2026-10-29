import json, sqlite3
DB = "/opt/bot-stack/astrbot/data/data_v4.db"
c = sqlite3.connect(DB, timeout=15)
raw = c.execute("select value from preferences where key='inactivated_plugins'").fetchone()[0]
data = json.loads(raw)
lst = data.get("val") or []
# 换成框架实际比对的 module_path 形式
lst = [x for x in lst if "GPT_SoVITS" not in x]
lst.append("data.plugins.astrbot_plugin_GPT_SoVITS.main")
data["val"] = lst
c.execute("update preferences set value=? where key='inactivated_plugins'",
          (json.dumps(data, ensure_ascii=False),))
c.commit()
chk = json.loads(c.execute("select value from preferences where key='inactivated_plugins'").fetchone()[0])["val"]
c.close()
print("改后:", chk)
