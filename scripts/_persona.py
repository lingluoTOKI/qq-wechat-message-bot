import sqlite3, json
c = sqlite3.connect("file:/AstrBot/data/data_v4.db?mode=ro", uri=True)
sp = c.execute("select system_prompt from personas where persona_id='12'").fetchone()[0]
c.close()
print("字符数:", len(sp))
print("=" * 70)
print(sp)
