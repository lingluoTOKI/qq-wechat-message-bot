"""关闭识图（24h 0 成功，纯浪费）并把默认超时从 30s 降到 12s"""
import json
import shutil
from datetime import datetime

stamp = datetime.now().strftime("%Y%m%d-%H%M")

# 1) 配置：关闭识图
CFG = "/opt/bot-stack/astrbot/data/config/spectrecore_config.json"
shutil.copy(CFG, CFG + ".bak-visionoff-" + stamp)
d = json.load(open(CFG, encoding="utf-8"))
ip = d.setdefault("image_processing", {})
old = ip.get("use_image_caption")
ip["use_image_caption"] = False
json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("use_image_caption: %s -> False" % old)

# 2) 代码：默认超时 30 -> 12（防止将来误开时拖 30 秒）
PY = "/opt/bot-stack/astrbot/data/plugins/spectrecore/utils/image_caption.py"
shutil.copy(PY, PY + ".bak-timeout-" + stamp)
s = open(PY, encoding="utf-8").read()
if "timeout: int = 30" in s:
    s = s.replace("timeout: int = 30", "timeout: int = 12", 1)
    open(PY, "w", encoding="utf-8").write(s)
    print("默认超时: 30s -> 12s")
else:
    print("未找到 'timeout: int = 30'，跳过（可能已改过）")
