"""一次性执行三项配置修复：
1. 预算提额：token 500,000 -> 600,000；费用日限 0.2 -> 0.3
2. livingmemory 日志级别降到 WARNING（消除 49 条 [DEBUG-Reflection] 噪音）
3. 补齐 plugin_log_levels.json（框架的 per-plugin 日志覆盖机制）
"""
import json
import os
import shutil
from datetime import datetime

STAMP = datetime.now().strftime("%Y%m%d-%H%M")
BASE = "/opt/bot-stack/astrbot/data"

# ---------- 1. 预算提额 ----------
CFG = BASE + "/plugin_data/astrbot_plugin_cost_control/config.json"
shutil.copy(CFG, CFG + ".bak-raise-" + STAMP)
d = json.load(open(CFG, encoding="utf-8"))
before = (d["budgets"]["global_daily"], d["budgets_cost"]["global_daily"])
d["budgets"]["global_daily"] = 600000
d["budgets_cost"]["global_daily"] = 0.3
json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("预算提额: token %s -> %s | 费用 ¥%s -> ¥%s" % (
    format(before[0], ","), format(d["budgets"]["global_daily"], ","),
    before[1], d["budgets_cost"]["global_daily"]))
print("  月限保持: token %s | 费用 ¥%s" % (
    format(d["budgets"]["global_monthly"], ","), d["budgets_cost"]["global_monthly"]))

# ---------- 2+3. 插件日志级别 ----------
LVL = BASE + "/config/plugin_log_levels.json"
if os.path.exists(LVL):
    shutil.copy(LVL, LVL + ".bak-" + STAMP)
    levels = json.load(open(LVL, encoding="utf-8"))
else:
    levels = {}
levels["astrbot_plugin_livingmemory"] = "WARNING"
json.dump(levels, open(LVL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("plugin_log_levels.json: %s" % levels)
print("  → livingmemory 降到 WARNING（需重启生效，LogManager 有内存缓存）")
