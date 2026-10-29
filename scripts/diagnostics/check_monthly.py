import json
import sqlite3

DB = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"
CFG = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json"

c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=3)

# 本月累计
r = c.execute(
    "SELECT COALESCE(SUM(COALESCE(token_input_other,0)+COALESCE(token_input_cached,0)"
    "+COALESCE(token_output,0)+COALESCE(injection_total,0)),0) "
    "FROM cost_supplements WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
).fetchone()
month = int(r[0] or 0)

r2 = c.execute(
    "SELECT COALESCE(SUM(COALESCE(token_input_other,0)+COALESCE(token_input_cached,0)"
    "+COALESCE(token_output,0)+COALESCE(injection_total,0)),0) "
    "FROM cost_supplements "
    "WHERE date(created_at)=(SELECT date(MAX(created_at)) FROM cost_supplements)"
).fetchone()
today = int(r2[0] or 0)

print("本月累计 token:", format(month, ","))
print("今日累计 token:", format(today, ","))
print()
for lim in (5000000, 15000000, 45000000):
    state = "超限" if month >= lim else "未超"
    print("  月限额 %10s -> %5.0f%%  %s" % (format(lim, ","), month / lim * 100, state))

print()
print("=== 当前 budgets_cost ===")
d = json.load(open(CFG))
print(" ", d.get("budgets_cost"))

print()
print("=== 历史备份的费用预算 ===")
import os
base = os.path.dirname(CFG)
for f in sorted(os.listdir(base)):
    if f.startswith("config.json.bak-") and ("20261023" in f or "20261024" in f or "20261025-13" in f or "budget-1625" in f):
        try:
            dd = json.load(open(os.path.join(base, f)))
            bc = dd.get("budgets_cost", {})
            print("  %-42s daily=%s monthly=%s" % (f, bc.get("global_daily"), bc.get("global_monthly")))
        except Exception:
            pass
