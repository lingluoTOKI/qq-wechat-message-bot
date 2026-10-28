import sqlite3
for p in ['/AstrBot/data/plugin_data/wushu_ops/assistant.sqlite',
          '/AstrBot/data/plugins/astrbot_plugin_wushu_ops/assistant.sqlite']:
    print(f"\n=== {p} ===")
    db = sqlite3.connect(p)
    try:
        rows = db.execute("SELECT k FROM state").fetchall()
        for r in rows:
            print(" ", r[0])
    except Exception as e:
        print(" error:", e)
    db.close()
