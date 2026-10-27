#!/usr/bin/env python3
"""确认 documents 是否有向量分可用（决定相似度闸门是否真的生效）。"""
import json
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db?mode=ro"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)

    print("=== documents 一条的 metadata 键 ===")
    row = c.execute("select doc_id, metadata from documents order by id desc limit 1").fetchone()
    if row:
        print("doc_id:", row[0])
        try:
            m = json.loads(row[1] or "{}")
            for k, v in m.items():
                vs = str(v)
                print("  %-22s %s" % (k, vs[:80] + ("…" if len(vs) > 80 else "")))
        except Exception as e:
            print("  metadata 解析失败:", e, str(row[1])[:200])

    print()
    print("=== 是否有向量表/列 ===")
    tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    print("  含 vec/embed 的表:", [t for t in tabs if "vec" in t.lower() or "embed" in t.lower()] or "(无)")
    cols = [r[1] for r in c.execute("pragma table_info(documents)")]
    print("  documents 列:", cols)

    print()
    print("=== metadata 里是否普遍带向量 ===")
    n_vec = 0
    n_all = 0
    for (md,) in c.execute("select metadata from documents"):
        n_all += 1
        try:
            m = json.loads(md or "{}")
        except Exception:
            continue
        if any("vec" in k.lower() or "embed" in k.lower() for k in m):
            n_vec += 1
    print("  %d/%d 条带向量字段" % (n_vec, n_all))
    c.close()


if __name__ == "__main__":
    main()
