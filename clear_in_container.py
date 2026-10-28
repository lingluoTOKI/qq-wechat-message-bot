import sqlite3
p = '/AstrBot/data/plugin_data/wushu_ops/assistant.sqlite'
db = sqlite3.connect(p)
rows = db.execute("SELECT k FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'").fetchall()
print("before:", rows)
db.execute("DELETE FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'")
db.commit()
rows = db.execute("SELECT k FROM state WHERE k LIKE '%rank%' OR k LIKE '%attempt%'").fetchall()
print("after:", rows)
db.close()
