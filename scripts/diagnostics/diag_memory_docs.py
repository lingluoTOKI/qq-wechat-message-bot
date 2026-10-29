#!/usr/bin/env python3
"""抽样 documents 表 + 列出 recall_engine 全部配置键，找长度上限开关。"""
import json
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db?mode=ro"
CFG = "/AstrBot/data/config/astrbot_plugin_livingmemory_config.json"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    cols = [r[1] for r in c.execute("pragma table_info(documents)")]
    print("documents 列:", cols)
    print("\n=== 抽样 3 条 ===")
    for row in c.execute("select length(text), substr(text,1,300) from documents order by length(text) desc limit 3"):
        print("-" * 66)
        print("  [%d 字]" % row[0])
        print("  " + (row[1] or "").replace("\n", "\n  "))
    c.close()

    print("\n" + "=" * 70)
    print("=== recall_engine 全部配置 ===")
    d = json.load(open(CFG, encoding="utf-8-sig"))
    re_ = d.get("recall_engine") or {}
    for k, v in re_.items():
        print("  %-40s = %s" % (k, v))

    print("\n=== 其他可能与长度相关的键 ===")
    def walk(o, p="", dep=0):
        if dep > 3:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v, p + "." + k, dep + 1)
                elif any(x in k.lower() for x in ("char", "len", "trunc", "max", "limit", "token")):
                    print("  %-46s = %s" % (p + "." + k, v))
    walk(d)


if __name__ == "__main__":
    main()
