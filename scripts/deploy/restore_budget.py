"""把 token 预算恢复为 10-25 13:22 那版（500k/日、5M/月、1M/单会话）。"""
import json
import os
import shutil
from datetime import datetime

CFG = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json"
stamp = datetime.now().strftime("%Y%m%d-%H%M")
bak = CFG + ".bak-restore500k-" + stamp

shutil.copyfile(CFG, bak)
print("备份 ->", os.path.basename(bak))

d = json.load(open(CFG, encoding="utf-8"))

before = dict(d.get("budgets", {}))
d["budgets"] = {
    "per_session_daily": 1000000,
    "per_user_daily": 0,
    "per_model_daily": 0,
    "global_daily": 500000,
    "global_monthly": 5000000,
}

json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print()
print("token 预算变化：")
for k in ("per_session_daily", "global_daily", "global_monthly"):
    print("  %-18s %10s -> %10s" % (k, format(before.get(k, 0), ","), format(d["budgets"][k], ",")))

print()
print("费用预算（未改动）：")
print(" ", d.get("budgets_cost"))
print()
print("超额动作:", d.get("default_on_exceeded"))
print("刷新时间:", d.get("refresh_time"))
