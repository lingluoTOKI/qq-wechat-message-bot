#!/usr/bin/env python3
"""验证假设：cache_events 的「变更」是否由不同 provider 交替触发。

做法：看 07:00-10:00 区间内 cost_supplements 里 provider_id 的时间分布，
以及 cache_events 的时间点是否紧跟在 MiMo 请求之后。
"""
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db?mode=ro"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)

    print("=== 各 provider 的请求数 / token / 缓存命中 ===")
    q = """
    select provider_id, provider_model, count(*) n,
           coalesce(sum(token_input_cached),0) ck,
           coalesce(sum(token_input_other),0) uu,
           coalesce(sum(token_output),0) ot
    from cost_supplements
    group by provider_id, provider_model
    order by n desc
    """
    for pid, model, n, ck, uu, ot in c.execute(q):
        rate = 100.0 * ck / (ck + uu) if (ck + uu) else 0
        print("  %-28s %-22s %4d次  命中%5.1f%%  未命中%8d" % (pid, model, n, rate, uu))

    print()
    print("=== 07:00-10:00 的请求时序（看是否 provider 交替）===")
    q2 = """
    select created_at, provider_id, provider_model,
           coalesce(token_input_other,0), coalesce(token_input_cached,0),
           coalesce(injection_total,0)
    from cost_supplements
    where created_at between '2026-10-25 23:00:00' and '2026-10-26 02:00:00'
    order by created_at
    """
    rows = list(c.execute(q2))
    print("  区间内 %d 条" % len(rows))
    for r in rows:
        print("  %s  %-24s %-18s in=%-7d cached=%-7d inj=%d" % r)

    print()
    print("=== cache_events 时间点（同一区间，UTC）===")
    q3 = """
    select created_at, type from cache_events
    where created_at between '2026-10-25 23:00:00' and '2026-10-26 02:00:00'
    order by created_at
    """
    for r in c.execute(q3):
        print("  %s  %s" % r)

    c.close()


if __name__ == "__main__":
    main()
