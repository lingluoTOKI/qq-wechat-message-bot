"""把费用日预算改成指定值（默认 0.2），只动 budgets_cost.global_daily"""
import json
import os
import shutil
import sys
from datetime import datetime

P = "/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_cost_control/config.json"
VAL = float(sys.argv[1]) if len(sys.argv) > 1 else 0.2

shutil.copy(P, P + ".bak-fee%s-%s" % (int(VAL * 10), datetime.now().strftime("%Y%m%d-%H%M")))
d = json.load(open(P, encoding="utf-8"))
old = d.get("budgets_cost", {}).get("global_daily")
d.setdefault("budgets_cost", {})["global_daily"] = VAL
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("费用日限: %s -> %s" % (old, VAL))
print("token 日限: %s (未动)" % d.get("budgets", {}).get("global_daily"))
print("费用月限: %s (未动)" % d.get("budgets_cost", {}).get("global_monthly"))
print("on_exceeded: %s" % d.get("default_on_exceeded"))
