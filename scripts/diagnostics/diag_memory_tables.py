#!/usr/bin/env python3
"""量 livingmemory 各表的实际内容体积，确认 top_k=3 召回的是哪张表。"""
import sqlite3

DB = "file:/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db?mode=ro"


def est(s):
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return int(cjk + (len(s) - cjk) / 3.5)


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    for t in ("documents", "memory_atoms", "graph_entries", "graph_nodes"):
        try:
            cols = [r[1] for r in c.execute("pragma table_info(%s)" % t)]
            n = c.execute("select count(*) from %s" % t).fetchone()[0]
            # 找文本列
            tc = None
            for cand in ("content", "text", "summary", "memory", "body", "name", "description"):
                if cand in cols:
                    tc = cand
                    break
            if not tc:
                print("%-16s %4d 条  (无文本列: %s)" % (t, n, cols[:5]))
                continue
            rows = [r[0] for r in c.execute("select %s from %s" % (tc, t))]
            lens = sorted(len(x or "") for x in rows)
            tk = sorted(est(x or "") for x in rows)
            if not lens:
                print("%-16s %4d 条  (空)" % (t, n))
                continue
            m = len(lens)
            print("%-16s %4d 条  文本列=%-10s 字符 平均%6.0f 中位%5d 最大%6d | token 平均%5.0f 最大%5d"
                  % (t, n, tc, sum(lens) / m, lens[m // 2], lens[-1],
                     sum(tk) / m, tk[-1]))
            print("    top3 最坏 token ≈ %d" % sum(sorted(tk, reverse=True)[:3]))
        except Exception as e:
            print("%-16s 读取失败: %s" % (t, e))
    c.close()


if __name__ == "__main__":
    main()
