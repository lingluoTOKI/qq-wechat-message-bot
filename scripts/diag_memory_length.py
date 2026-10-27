#!/usr/bin/env python3
"""统计 livingmemory 记忆长度，估算每轮注入的真实开销。

注入方式：recall_engine.injection_method = extra_user_content
          top_k = 3（召回）+ recent_memory_count = 1（近期）
"""
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db?mode=ro"


def est_tokens(s):
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return int(cjk + (len(s) - cjk) / 3.5)


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    print("表:", tabs)

    # 找记忆主表
    target = None
    for t in tabs:
        if "memor" in t.lower() and "graph" not in t.lower():
            cols = [r[1] for r in c.execute("pragma table_info(%s)" % t)]
            if any("content" in x.lower() or "text" in x.lower() for x in cols):
                target = (t, cols)
                break
    if not target:
        print("!! 未找到记忆内容表")
        return
    t, cols = target
    print("\n主表 %s 列: %s" % (t, cols))

    ccol = next((x for x in cols if x.lower() in ("content", "text", "memory")), cols[1])
    total = c.execute("select count(*) from %s" % t).fetchone()[0]
    print("记忆条数: %d" % total)

    rows = list(c.execute("select %s from %s" % (ccol, t)))
    lens = sorted((len(r[0] or ""), est_tokens(r[0] or "")) for r in rows)
    if not lens:
        print("(空表)")
        return
    n = len(lens)
    avg_c = sum(x[0] for x in lens) / n
    avg_t = sum(x[1] for x in lens) / n
    print("字符长度: 平均 %.0f / 中位 %d / 最大 %d" % (avg_c, lens[n // 2][0], lens[-1][0]))
    print("token 估算: 平均 %.0f / 中位 %d / 最大 %d" % (avg_t, lens[n // 2][1], lens[-1][1]))

    # 最长的 5 条
    print("\n最长的 5 条:")
    for row in c.execute("select length(%s), substr(%s,1,110) from %s order by length(%s) desc limit 5"
                         % (ccol, ccol, t, ccol)):
        print("  %4d字  %s" % (row[0], (row[1] or "").replace("\n", " ")))

    # 典型注入估算：3 条召回 + 1 条近期
    print("\n=== 每轮注入估算（top_k=3 + recent=1）===")
    print("  按平均长度: 4 条 × %.0f token ≈ %.0f token" % (avg_t, avg_t * 4))
    print("  按中位长度: 4 条 × %d token ≈ %d token" % (lens[n // 2][1], lens[n // 2][1] * 4))
    top4 = sorted((x[1] for x in lens), reverse=True)[:4]
    print("  最坏情况 (最长4条): ≈ %d token" % sum(top4))
    c.close()


if __name__ == "__main__":
    main()
