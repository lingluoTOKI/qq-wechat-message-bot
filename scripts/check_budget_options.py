import sqlite3

DB = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"
c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=3)
r = c.execute(
    "SELECT COALESCE(SUM(COALESCE(token_input_other,0)+COALESCE(token_input_cached,0)"
    "+COALESCE(token_output,0)+COALESCE(injection_total,0)),0) "
    "FROM cost_supplements "
    "WHERE date(created_at)=(SELECT date(MAX(created_at)) FROM cost_supplements)"
).fetchone()
used = int(r[0] or 0)
print("今日已用 token:", format(used, ","))
print()
for lim in (500000, 800000, 1000000, 1200000, 2200000):
    pct = used / lim * 100
    state = "已超限 -> 机器人停止回复" if used >= lim else "未超限 -> 还可继续"
    left = lim - used
    print("  限额 %9s  %5.0f%%  剩余 %9s  %s" % (
        format(lim, ","), pct, format(max(left, 0), ","), state))
