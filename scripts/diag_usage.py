"""诊断 /用量 指令与 cost_control 实际统计的差异"""
import json
import sqlite3
import os
from datetime import datetime, timedelta

DB = "/AstrBot/data/data_v4.db"
COSTDB = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"
CFG = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json"
CALIB = 1.5


def q(db, sql, args=()):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()
    except Exception as e:
        print("  [ERR] %s" % e)
        return []


print("=" * 56)
print("时间基准")
print("=" * 56)
now_utc = datetime.utcnow()
print("容器 UTC 现在 : %s" % now_utc.strftime("%Y-%m-%d %H:%M"))
try:
    from zoneinfo import ZoneInfo
    now_cst = datetime.now(ZoneInfo("Asia/Shanghai"))
    print("北京 现在     : %s" % now_cst.strftime("%Y-%m-%d %H:%M"))
    print("UTC 日期 vs 北京日期: %s vs %s %s" % (
        now_utc.strftime("%m-%d"), now_cst.strftime("%m-%d"),
        "一致" if now_utc.strftime("%Y-%m-%d") == now_cst.strftime("%Y-%m-%d") else "★不一致（跨日错位）"))
except Exception as e:
    print("ZoneInfo 不可用: %s" % e)

print()
print("=" * 56)
print("数据源 A：provider_stats（/用量 用的）")
print("=" * 56)
for label, d in (("UTC今天", now_utc.strftime("%Y-%m-%d")),
                 ("北京今天", datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"))):
    rows = q(DB, """select count(*), coalesce(sum(token_input_other),0),
                    coalesce(sum(token_input_cached),0), coalesce(sum(token_output),0)
             from provider_stats where provider_id like 'deepseek%' and date(created_at)>=?""", (d,))
    if rows:
        n, miss, hit, out = rows[0]
        print("%s: 请求 %s  未缓存 %s  缓存 %s  输出 %s  合计 %s" % (
            label, n, miss, hit, out, format(int(miss + hit + out), ",")))

print()
print("  --- 全部 provider 分组（UTC今天）---")
for r in q(DB, """select provider_id, count(*),
                  coalesce(sum(token_input_other+token_input_cached+token_output),0)
           from provider_stats where date(created_at)>=? group by provider_id""",
           (now_utc.strftime("%Y-%m-%d"),)):
    print("  %-28s %4s 次  %s token" % (r[0], r[1], format(int(r[2]), ",")))

print()
print("=" * 56)
print("数据源 B：cost_control supplement.db（真正决定停机用的）")
print("=" * 56)
try:
    c = sqlite3.connect("file:%s?mode=ro" % COSTDB, uri=True, timeout=5)
    cols = [r[1] for r in c.execute("PRAGMA table_info(cost_supplements)")]
    print("字段: %s" % ", ".join(cols))
    row = c.execute("""select coalesce(sum(coalesce(token_input_other,0)+coalesce(token_input_cached,0)
                        +coalesce(token_output,0)+coalesce(injection_total,0)),0),
                        coalesce(sum(cost_amount),0)
                 from cost_supplements where date(created_at)=date('now')""").fetchone()
    print("今日(UTC now) 合计 token: %s   费用: $%.6f" % (format(int(row[0]), ","), row[1]))
    c.close()
except Exception as e:
    print("  [ERR] %s" % e)

print()
print("=" * 56)
print("预算配置")
print("=" * 56)
try:
    d = json.load(open(CFG, encoding="utf-8"))
    print("budgets     : %s" % d.get("budgets"))
    print("budgets_cost: %s" % d.get("budgets_cost"))
    print("on_exceeded : %s" % d.get("default_on_exceeded"))
    print("refresh_time: %s" % d.get("refresh_time"))
except Exception as e:
    print("  [ERR] %s" % e)

print()
print("=" * 56)
print("差异结论")
print("=" * 56)
rows = q(DB, """select coalesce(sum(token_input_other+token_input_cached+token_output),0)
         from provider_stats where provider_id like 'deepseek%' and date(created_at)>=?""",
         (now_utc.strftime("%Y-%m-%d"),))
a = int(rows[0][0]) if rows and rows[0] else 0
a_cal = int(round(a * CALIB))
try:
    c = sqlite3.connect("file:%s?mode=ro" % COSTDB, uri=True, timeout=5)
    b = int(c.execute("""select coalesce(sum(coalesce(token_input_other,0)+coalesce(token_input_cached,0)
                        +coalesce(token_output,0)+coalesce(injection_total,0)),0)
                 from cost_supplements where date(created_at)=date('now')""").fetchone()[0])
    c.close()
except Exception:
    b = 0
print("A provider_stats 原始       : %s" % format(a, ","))
print("A ×1.5 校准后（/用量显示）  : %s" % format(a_cal, ","))
print("B cost_control 实际判定值   : %s" % format(b, ","))
print("A校准 vs B 偏差             : %+.1f%%" % ((a_cal - b) / b * 100 if b else 0))
print("原始 A vs B 偏差            : %+.1f%%" % ((a - b) / b * 100 if b else 0))
print()
tok_lim = 0
try:
    tok_lim = int(json.load(open(CFG, encoding="utf-8")).get("budgets", {}).get("global_daily") or 0)
except Exception:
    pass
if tok_lim:
    print("按显示值 %s / %s = %.0f%%  ← 用户看到的" % (format(a_cal, ","), format(tok_lim, ","), a_cal / tok_lim * 100))
    print("按判定值 %s / %s = %.0f%%  ← 机器人真实状态" % (format(b, ","), format(tok_lim, ","), b / tok_lim * 100))
    print("机器人应处于: %s" % ("已超限（只回休眠提示）" if b >= tok_lim else "正常"))
