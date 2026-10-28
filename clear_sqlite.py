import sqlite3, os
p = '/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/assistant.sqlite'
db = sqlite3.connect(p)
rows = db.execute("SELECT k FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'").fetchall()
print("before:", rows)
db.execute("DELETE FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'")
db.commit()
rows = db.execute("SELECT k FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'").fetchall()
print("after:", rows)
db.close()
