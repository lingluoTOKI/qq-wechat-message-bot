"""把 clonetts 切到 MiMo TTS（voiceclone 模式，音色已固化为参考音频）"""
import json
import shutil
from datetime import datetime

CFG = "/opt/bot-stack/astrbot/data/config/astrbot_plugin_clonetts_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
DESC = ("十一二岁的女孩声，音调极高又清脆，带一点奶音但声音结实有力、很有精神，"
        "中气足、穿透力强，语速轻快有跳跃感，句尾利落上扬。"
        "语气是活泼外放、元气满满的，嘴上却故作老练地傲娇，"
        "说着不服气的话时带着一点点挑衅和娇嗔，尾音黏黏地上扬，像在跟人斗嘴又像在撒娇，"
        "甜而不软，吵而不凶。")

shutil.copy(CFG, CFG + ".bak-mimo-" + datetime.now().strftime("%Y%m%d-%H%M"))
d = json.load(open(CFG, encoding="utf-8"))
before = d.get("voice_type")
d["voice_type"] = "mimo-v2.5-tts-voiceclone"
d["mimo_tts_ref_audio"] = REF
d["mimo_tts_voice_desc"] = DESC
d["mimo_tts_api_base"] = ""
d["mimo_tts_api_key"] = ""
d["mimo_tts_style_prompt"] = ""
d["fallback_voice_type"] = "zh-CN-XiaoyiNeural"
json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("voice_type      : %s -> %s" % (before, d["voice_type"]))
print("参考音频        : %s" % d["mimo_tts_ref_audio"])
print("回落音色        : %s" % d["fallback_voice_type"])
print("edge 音调       : %s" % d.get("edge_pitch"))
print("tts_probability : %s (仅明确要求时出语音)" % d.get("tts_probability"))
print("max_length      : %s" % d.get("max_length"))
