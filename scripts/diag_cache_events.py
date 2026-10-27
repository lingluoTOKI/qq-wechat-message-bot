#!/usr/bin/env python3
"""分析 cache_events：定位 system prompt / tools 到底哪部分在变。"""
import json
import sqlite3
import difflib
import sys

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def flat(o):
    if isinstance(o, str):
        return o
    return json.dumps(o, ensure_ascii=False)


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    rows = c.execute(
        "select before,after,created_at,umo from cache_events "
        "where type='system_prompt_change' order by id desc limit 3"
    ).fetchall()
    c.close()

    for idx, (b, a, ts, umo) in enumerate(rows, 1):
        bb = json.loads(b) if b else None
        aa = json.loads(a) if a else None
        print("=" * 72)
        print("[事件 %d] %s  umo=%s" % (idx, ts, umo))
        print("before type=%s  after type=%s" % (type(bb).__name__, type(aa).__name__))
        if isinstance(bb, dict):
            print("before keys:", list(bb.keys()))
        if isinstance(aa, dict):
            print("after  keys:", list(aa.keys()))

        bs, as_ = flat(bb), flat(aa)
        print("长度 before=%d after=%d" % (len(bs), len(as_)))

        sm = difflib.SequenceMatcher(None, bs, as_)
        n = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            n += 1
            print("  [%s] before=%r" % (tag, bs[i1:i2][:160]))
            print("         after =%r" % (as_[j1:j2][:160]))
            if n >= 10:
                print("  ...(差异过多，已截断)")
                break
        if n == 0:
            print("  (无差异 → 可能是长度/哈希比较引发的误报)")


if __name__ == "__main__":
    main()
