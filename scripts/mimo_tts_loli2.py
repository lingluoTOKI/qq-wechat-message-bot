"""元气萝莉 · 力量感强化版
问题诊断：O 版写了「音量不大」+「奶音」+ 纯头腔共鸣 → 声音薄、飘、没劲
解法：保留幼态特征（高音/头腔/奶音），补上支撑（胸腔共鸣/中气/结实/穿透力/爆发力）
"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

CFG = "/AstrBot/data/cmd_config.json"
OUT_DIR = os.environ.get("OUT_DIR", "/AstrBot/data/plugin_data/mimo_tts_samples")
os.makedirs(OUT_DIR, exist_ok=True)

d = json.load(open(CFG, encoding="utf-8"))
KEY = BASE = ""
for s in d.get("provider_sources", []):
    if str(s.get("provider", "")).lower() == "xiaomi":
        k = s.get("key", "")
        KEY = (k[0] if isinstance(k, list) and k else k) or ""
        BASE = s.get("api_base", "") or ""
        break
if not BASE:
    BASE = "https://api.xiaomimimo.com/v1"

VOICES = [
    # R：O 的描述 + 力量感补强（去掉「音量不大」，加中气/胸腔支撑/结实）
    ("R_元气加强",
     "很年轻的女孩声，听起来十一二岁，音调高而明亮，音色清脆偏细但**结实有支撑感**，"
     "以头腔共鸣为主并带一点胸腔共鸣，声音中气很足、爆发力强、有穿透力，一点也不虚也不飘，"
     "带一点点奶音但绝不软趴趴，语速轻快，节奏跳动感强，咬字利落有力，"
     "句尾干净地向上扬，整个人像小太阳一样元气外放，热情有冲劲，像随时要跳起来大喊。",
     "喂喂喂！听到没有！本小姐——啊不对，我可是大肥鱼诶！"
     "有什么需要尽管说，交给我啦！"),

    # S：更极端的"结实有力"路线，参考战斗女主的中气支撑但保留幼态音高
    ("S_结实穿透",
     "十一二岁的小女孩声，音调极高且清脆，但声音结实有重量感、像一根绷紧的弦，"
     "完全不是软绵绵的童声；头腔共鸣明亮，同时胸腔共鸣托住，"
     "中气极足、每个字都送出去有穿透力，音量饱满不虚，"
     "语速快而利落，节奏跳跃，句尾斩钉截铁地上扬，"
     "元气到像个小太阳在面前蹦，能量感爆棚。",
     "我可是大肥鱼诶！这点事情交给我，三分钟搞定！"
     "你就好好看着吧——不信你听！"),

    # T：元气 + 傲娇（用户的完整诉求：元气 + 傲娇 + 萝莉）
    ("T_元气傲娇",
     "十一二岁的女孩声，音调极高又清脆，带一点奶音但声音结实有力、很有精神，"
     "中气足、穿透力强，语速轻快有跳跃感，句尾利落上扬。"
     "语气是活泼外放、元气满满的，嘴上却故作老练地傲娇，"
     "说着不服气的话时带着一点点挑衅和娇嗔，尾音黏黏地上扬，像在跟人斗嘴又像在撒娇，"
     "甜而不软，吵而不凶。",
     "哼，本小姐出手还不是手到擒来！"
     "你呀，就乖乖等着听好消息吧——才、才不是想让你夸我呢！"),
]


def synth(model, desc, text, tag):
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": desc},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav"},
    }
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("  HTTP %s  %s" % (e.code, e.read().decode()[:200].replace("\n", " ")))
        return None
    except Exception as e:
        print("  ERR", type(e).__name__, str(e)[:120])
        return None
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    audio = msg.get("audio")
    raw = (audio.get("data") if isinstance(audio, dict) else audio) or ""
    if not raw:
        print("  无音频:", json.dumps(j, ensure_ascii=False)[:220])
        return None
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    path = os.path.join(OUT_DIR, "%s.wav" % tag)
    with open(path, "wb") as f:
        f.write(base64.b64decode(raw))
    print("  ✓ %s  %.1f KB" % (tag, os.path.getsize(path) / 1024))
    return path


model = os.environ.get("MODEL", "mimo-v2.5-tts-voicedesign")
print("模型: %s\n" % model)
for tag, desc, text in VOICES:
    print("[%s]" % tag)
    synth(model, desc, text, tag)
    time.sleep(8)
print("\n输出: %s" % OUT_DIR)
