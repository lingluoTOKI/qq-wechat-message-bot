#!/usr/bin/env python3
"""完整转储人格表，用于确认 skills/tools 字段的有无与取值。"""
import json
import sqlite3

DB = "file:/AstrBot/data/data_v4.db?mode=ro"

MAXLEN = 120


def short(v):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= MAXLEN else s[:MAXLEN] + "…"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    cands = [t for t in tabs if "persona" in t.lower()]
    print("候选表:", cands)
    for t in cands:
        cols = [r[1] for r in c.execute("pragma table_info(%s)" % t)]
        print("\n" + "=" * 72)
        print("表 %s 列: %s" % (t, cols))
        rows = list(c.execute("select * from %s" % t))
        print("行数: %d" % len(rows))
        for row in rows:
            rec = dict(zip(cols, row))
            print("-" * 66)
            for k in cols:
                v = rec[k]
                if v is None:
                    print("  %-16s = None" % k)
                else:
                    print("  %-16s = %s" % (k, short(v)))
    c.close()


if __name__ == "__main__":
    main()
