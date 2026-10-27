#!/usr/bin/env python3
"""看缓存命中率的实时变化：整体 / 今日 / 各时段，判断是否被重启打低。"""
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def row(label, n, ck, uu, ot=0):
    tot = ck + uu
    rate = 100.0 * ck / tot if tot else 0
    print("  %-26s 请求%4d  命中%9s  未命中%9s  命中率%6.1f%%"
          % (label, n, format(ck, ","), format(uu, ","), rate))


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)

    q = """
    select count(*),
           coalesce(sum(token_input_cached),0),
           coalesce(sum(token_input_other),0),
           coalesce(sum(token_output),0)
    from cost_supplements {where}
    """

    row("全部历史", *c.execute(q.format(where="")).fetchone()[:3])
    row("今日（本地00:00起）",
        *c.execute(q.format(where="where created_at >= '2026-10-25 16:00:00'")).fetchone()[:3])
    row("最近 3 小时",
        *c.execute(q.format(where="where created_at >= '2026-10-26 07:00:00'")).fetchone()[:3])
    row("最近 1 小时",
        *c.execute(q.format(where="where created_at >= '2026-10-26 09:00:00'")).fetchone()[:3])

    print()
    print("=== 今天逐条明细（时间 / 命中 / 未命中 / 命中率）===")
    for ts, ck, uu in c.execute(
        "select created_at, coalesce(token_input_cached,0), coalesce(token_input_other,0) "
        "from cost_supplements where created_at >= '2026-10-25 16:00:00' "
        "order by created_at"
    ):
        tot = ck + uu
        r = 100.0 * ck / tot if tot else 0
        bar = "#" * int(r / 5)
        print("  %s  命中%6d 未命中%6d  %5.1f%% %s" % (ts[:19], ck, uu, r, bar))
    c.close()


if __name__ == "__main__":
    main()
