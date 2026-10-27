"""关闭「显示工具调用结果/状态」——这两项昨天被误打开，导致工具调用内容直接发进聊天框"""
import json
import shutil
from datetime import datetime

C = "/opt/bot-stack/astrbot/data/cmd_config.json"
shutil.copy(C, C + ".bak-tooldisplay-" + datetime.now().strftime("%Y%m%d-%H%M"))

raw = open(C, "rb").read()
has_bom = raw[:3] == b"\xef\xbb\xbf"
with open(C, encoding="utf-8-sig") as f:
    d = json.load(f)

ps = d.setdefault("provider_settings", {})
before = (ps.get("show_tool_call_result"), ps.get("show_tool_use_status"))
ps["show_tool_call_result"] = False
ps["show_tool_call_result"] = False
ps["show_tool_use_status"] = False

with open(C, "w", encoding="utf-8-sig" if has_bom else "utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print("BOM: %s" % ("有（已保留）" if has_bom else "无"))
print("show_tool_call_result: %s -> %s" % (before[0], ps["show_tool_call_result"]))
print("show_tool_use_status: %s -> %s" % (before[1], ps["show_tool_use_status"]))
print("其余 provider_settings 键数: %d（未动）" % len(ps))
