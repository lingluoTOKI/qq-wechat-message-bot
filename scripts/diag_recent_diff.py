#!/usr/bin/env python3
"""只看最近的 system_prompt_change，打印完整 diff（不截断）。

早期事件的 before 里没有 system_diff 字段，diff 会退化成"全部新增"，
因此这里只取最近 3 条，它们的结构一致，diff 可信。
"""
import json
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    rows = c.execute(
        "select before,after,created_at from cache_events "
        "where type='system_prompt_change' order by id desc limit 3"
    ).fetchall()
    c.close()

    for idx, (b, a, ts) in enumerate(rows, 1):
        before = json.loads(b) if b else {}
        after = json.loads(a) if a else {}
        print("=" * 74)
        print("[%d] %s" % (idx, ts))
        print("  history_len %s -> %s" % (before.get("history_len"), after.get("history_len")))
        print("  system_hash %s -> %s" % (before.get("system_hash"), after.get("system_hash")))
        print("  tools_hash  %s -> %s" % (before.get("tools_hash"), after.get("tools_hash")))
        print("  contexts    %s -> %s" % (before.get("contexts_count"), after.get("contexts_count")))

        diff = after.get("system_diff")
        if not diff:
            print("  (无 system_diff)")
            continue
        add = sum(1 for d in diff if d.get("op") == "+")
        rem = sum(1 for d in diff if d.get("op") == "-")
        eq = sum(1 for d in diff if d.get("op") == " ")
        print("  diff: 相同 %d 行 / 新增 %d 行 / 删除 %d 行" % (eq, add, rem))
        shown = 0
        for d in diff:
            op = d.get("op")
            if op == " ":
                continue
            shown += 1
            mark = "+++" if op == "+" else "---"
            print("   %s %s" % (mark, (d.get("text") or "")[:200]))
        if shown == 0:
            print("   (内容无差异 → 疑似 hash 比较误报)")


if __name__ == "__main__":
    main()
