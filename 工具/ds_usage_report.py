#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 用量对账工具
用法：
  python ds_usage_report.py <zip或csv路径> [--by-hour]

会自动解压 zip，读取 amount-*.csv 与 cost-*.csv，输出：
  - 当日总 token（缓存命中/未命中/输出）与请求数
  - 平台实际费用（CNY）
  - 按小时分布（--by-hour）
  - 用官方单价复算，校验平台账单是否一致

官方单价（deepseek-flash，每百万 token，美元）：
  谷时：miss 0.15 / hit 0.003 / output 0.60
  峰时（工作日北京 09:00-12:00、14:00-18:00）：×2
"""
import sys, os, csv, zipfile, tempfile, io
from collections import defaultdict

PRICE_OFF = {"miss": 0.15, "hit": 0.003, "out": 0.60}   # USD / 1M
PEAK = 2.0


def load(path):
    if path.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp()
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        base = tmp
    else:
        base = os.path.dirname(os.path.abspath(path))
    amount = cost = None
    for name in os.listdir(base):
        if name.startswith("amount-") and name.endswith(".csv"):
            amount = os.path.join(base, name)
        elif name.startswith("cost-") and name.endswith(".csv"):
            cost = os.path.join(base, name)
    return amount, cost


def read_rows(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    amount_p, cost_p = load(sys.argv[1])
    by_hour = "--by-hour" in sys.argv

    tot = defaultdict(int)
    req = 0
    hours = defaultdict(lambda: defaultdict(int))
    for r in read_rows(amount_p):
        t = r["type"]
        v = int(float(r["amount"] or 0))
        h = r["start_time_iso"][11:13]
        if t == "request_count":
            req += v
            hours[h]["req"] += v
        else:
            key = {"input_cache_hit_tokens": "hit",
                   "input_cache_miss_tokens": "miss",
                   "output_tokens": "out"}[t]
            tot[key] += v
            hours[h][key] += v

    paid = sum(float(r["cost"]) for r in read_rows(cost_p))
    cur = read_rows(cost_p)[0]["currency"]

    # 复算：周一~周五 09-12、14-18 视为峰时
    import datetime
    day = read_rows(amount_p)[0]["start_time_iso"][:10]
    wd = datetime.date.fromisoformat(day).weekday()  # 0=周一
    peak = 0
    for h, d in hours.items():
        hh = int(h)
        is_peak = wd < 5 and (9 <= hh < 12 or 14 <= hh < 18)
        peak += 1 if is_peak else 0

    # 精确到小时复算
    estimate = 0.0
    for h, d in hours.items():
        hh = int(h)
        mult = PEAK if (wd < 5 and (9 <= hh < 12 or 14 <= hh < 18)) else 1.0
        estimate += (d["miss"] * PRICE_OFF["miss"] + d["hit"] * PRICE_OFF["hit"] + d["out"] * PRICE_OFF["out"]) * mult / 1e6

    print(f"日期: {day}  周{'一二三四五六日'[wd]}")
    print(f"请求数: {req}")
    print(f"缓存命中: {tot['hit']:,}   缓存未命中: {tot['miss']:,}   输出: {tot['out']:,}")
    print(f"总 token: {tot['hit'] + tot['miss'] + tot['out']:,}")
    print(f"平台账单: {paid:.6f} {cur}")
    print(f"按官方价复算: ${estimate:.6f}（峰时时段 {peak} 个）")

    if by_hour:
        print("\n北京时 | 请求 | 命中 | 未命中 | 输出")
        for h in sorted(hours):
            d = hours[h]
            print(f"{h}时  | {d['req']:4d} | {d['hit']:7,d} | {d['miss']:7,d} | {d['out']:6,d}")


if __name__ == "__main__":
    main()
