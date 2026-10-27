"""写入 clonetts 配置（注意：AstrBot 面板保存过的配置带 UTF-8 BOM）"""
import json
import shutil
from datetime import datetime

C = "/opt/bot-stack/astrbot/data/config/astrbot_plugin_clonetts_config.json"
shutil.copy(C, C + ".bak-split-" + datetime.now().strftime("%Y%m%d-%H%M"))
with open(C, encoding="utf-8-sig") as f:
    d = json.load(f)
d["tts_split_sentences"] = True
d["tts_max_parts"] = 4
d["tts_part_interval"] = 0.4
d["mimo_tts_default_tag"] = ""
with open(C, "w", encoding="utf-8-sig") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
print("配置: 分句=%s 最多%s段 间隔%ss 标签=%s" % (
    d["tts_split_sentences"], d["tts_max_parts"], d["tts_part_interval"],
    d["mimo_tts_default_tag"] or "自动"))
print("voice_type =", d.get("voice_type"))
