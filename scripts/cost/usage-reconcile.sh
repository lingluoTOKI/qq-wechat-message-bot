#!/bin/bash
# 对账：官方 DeepSeek 导出 CSV vs 本地统计
# 用法: usage-reconcile.sh [csv目录]  （默认 /opt/bot-stack/usage-csv）
set -e
DIR=${1:-/opt/bot-stack/usage-csv}
python3 - "$DIR" <<'PY'
import sys, glob, csv, collections, subprocess

d = sys.argv[1]
amount_files = glob.glob(d + "/amount-*.csv")
cost_files = glob.glob(d + "/cost-*.csv")
if not amount_files:
    print("未找到 amount-*.csv（目录：%s）" % d)
    sys.exit(1)

agg = collections.Counter()
req = 0
for f in amount_files:
    with open(f, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            t = r["type"]
            v = float(r["amount"])
            if t == "request_count":
                req += int(v)
            else:
                agg[t] += v

cost = 0.0
for f in cost_files:
    with open(f, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            cost += float(r["cost"])

hit = agg["input_cache_hit_tokens"]
miss = agg["input_cache_miss_tokens"]
out = agg["output_tokens"]
tot = hit + miss + out
rate = (100.0 * hit / (hit + miss)) if (hit + miss) else 0.0

print("== 官方账单 ==")
print("  请求 %d | token %d（命中 %d / 未命中 %d / 输出 %d）" % (req, tot, hit, miss, out))
print("  费用 ￥%.4f | 命中率 %.1f%%" % (cost, rate))

code = (
    "import sqlite3;"
    "c=sqlite3.connect('/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db');"
    "r=c.execute(\"select count(*),coalesce(sum(token_input_other),0),"
    "coalesce(sum(token_input_cached),0),coalesce(sum(token_output),0),"
    "coalesce(sum(cost_amount),0) from cost_supplements "
    "where date(created_at)=date('now','localtime')\").fetchone();"
    "print('%d|%d|%d|%d|%.6f'%r)"
)
q = subprocess.run(["docker", "exec", "-i", "astrbot", "python", "-c", code],
                   capture_output=True, text=True)
parts = q.stdout.strip().split("|")
if len(parts) >= 5:
    ln_n, ln_miss, ln_hit, ln_out, ln_cost = (
        int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), float(parts[4]))
    ln_tot = ln_miss + ln_hit + ln_out
    ln_cny = ln_cost * 6.67
    print("== 本地统计 ==")
    print("  请求 %d | token %d | 费用 ￥%.4f" % (ln_n, ln_tot, ln_cny))
    print("== 偏差（本地 vs 官方） ==")
    print("  请求 %+d%% | token %+.0f%% | 费用 %+.0f%%" % (
        100 * (ln_n - req) / req if req else 0,
        100 * (ln_tot - tot) / tot if tot else 0,
        100 * (ln_cny - cost) / cost if cost else 0))
else:
    print("本地统计读取失败：%s" % (q.stderr or q.stdout)[:200])
PY
