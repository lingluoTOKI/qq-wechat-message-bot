import json
p = "/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_cost_control/config.json"
d = json.load(open(p, encoding="utf-8-sig"))
print("token 日限 :", format(d["budgets"]["global_daily"], ","))
print("费用 日限  :", d["budgets_cost"]["global_daily"])
print("token 月限 :", format(d["budgets"].get("global_monthly", 0), ","))
print("费用 月限  :", d["budgets_cost"].get("global_monthly"))
print("on_exceeded:", d.get("default_on_exceeded"))
print("键:", [k for k in d if "exceed" in k.lower() or "budget" in k.lower()])
