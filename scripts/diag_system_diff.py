#!/usr/bin/env python3
"""聚合 cache_events 的 system_diff，统计哪些片段在反复增删。

目的：找出 system prompt 里"时有时无"的易变块，它们是前缀缓存失效的元凶。
"""
import json
import sqlite3
from collections import Counter

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def norm(text, width=90):
    t = " ".join((text or "").split())
    return t[:width]


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    rows = c.execute(
        "select after from cache_events where type='system_prompt_change' "
        "order by id desc limit 200"
    ).fetchall()
    c.close()

    added = Counter()
    removed = Counter()
    total = 0
    no_diff = 0

    for (raw,) in rows:
        total += 1
        try:
            d = json.loads(raw) if raw else {}
        except Exception:
            continue
        diff = d.get("system_diff")
        if not diff:
            no_diff += 1
            continue
        for item in diff:
            op = item.get("op")
            txt = norm(item.get("text"))
            if not txt:
                continue
            if op == "+":
                added[txt] += 1
            elif op == "-":
                removed[txt] += 1

    print("样本 %d 条 system_prompt_change（无 system_diff 的 %d 条）" % (total, no_diff))
    print()
    print("=== 被「加入」最多的片段（说明它平时不在，偶尔出现）===")
    for txt, n in added.most_common(18):
        print("  %3d次  %s" % (n, txt))
    print()
    print("=== 被「删除」最多的片段（说明它平时在，偶尔消失）===")
    for txt, n in removed.most_common(18):
        print("  %3d次  %s" % (n, txt))


if __name__ == "__main__":
    main()
