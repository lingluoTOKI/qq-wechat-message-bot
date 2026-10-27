#!/usr/bin/env python3
"""把每日预算提高到 token 750,000 / 费用 ¥0.45。保留 BOM 与原有格式。"""
import json
import shutil
from datetime import datetime

P = "/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_cost_control/config.json"


def main():
    raw = open(P, "rb").read()
    bom = raw[:3] == b"\xef\xbb\xbf"
    shutil.copy(P, "%s.bak-raise-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M")))

    d = json.load(open(P, encoding="utf-8-sig"))
    old_t = d["budgets"]["global_daily"]
    old_c = d["budgets_cost"]["global_daily"]
    d["budgets"]["global_daily"] = 750000
    d["budgets_cost"]["global_daily"] = 0.45
    with open(P, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

    print("token 日限: %s -> %s" % (format(old_t, ","), format(d["budgets"]["global_daily"], ",")))
    print("费用 日限 : %.2f -> %.2f" % (old_c, d["budgets_cost"]["global_daily"]))
    print("月限未动  : token %s / 费用 %.1f"
          % (format(d["budgets"].get("global_monthly", 0), ","),
             d["budgets_cost"].get("global_monthly", 0)))
    print("on_exceeded:", d.get("default_on_exceeded"))


if __name__ == "__main__":
    main()
