#!/usr/bin/env python3
"""检查 cost_supplements 的辅助字段，看能否区分「主回复」与「内部调用」。"""
import json
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def show(label, raw, limit=700):
    if not raw:
        print("  %s: (空)" % label)
        return
    try:
        d = json.loads(raw)
        s = json.dumps(d, ensure_ascii=False)
    except Exception:
        s = str(raw)
    print("  %s: %s" % (label, s[:limit]))


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    rows = list(c.execute(
        "select created_at, conversation_id, attribution, billing_context, raw_usage, "
        "matched_tier, injection_total from cost_supplements order by id desc limit 4"
    ))
    for r in rows:
        print("=" * 74)
        print("时间:", r[0], "| conversation:", r[1], "| tier:", r[5], "| inj:", r[6])
        show("attribution", r[2])
        show("billing_context", r[3])
        show("raw_usage", r[4], 400)

    print()
    print("=== conversation_id 分布 ===")
    for r in c.execute("select conversation_id, count(*) from cost_supplements group by 1 order by 2 desc limit 8"):
        print("  %-40s %4d" % (str(r[0])[:40], r[1]))
    c.close()


if __name__ == "__main__":
    main()
