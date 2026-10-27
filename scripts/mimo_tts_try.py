"""用 MiMo TTS (voicedesign) 试听：不需要任何参考音频，纯文字描述生成音色
用法: python3 mimo_tts_try.py  (在容器内跑，凭据从 cmd_config.json 读)
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

CFG = "/AstrBot/data/cmd_config.json"
OUT_DIR = os.environ.get("OUT_DIR", "/AstrBot/data/plugin_data/mimo_tts_samples")
os.makedirs(OUT_DIR, exist_ok=True)

d = json.load(open(CFG, encoding="utf-8"))
KEY = BASE = ""
for s in d.get("provider_sources", []):
    if s.get("type") == "mimo_tts_api" or s.get("id") == "xiaomi_tts":
        KEY = s.get("api_key", "")
        BASE = s.get("api_base", "")
        break
if not KEY:
    for s in d.get("provider_sources", []):
        if str(s.get("provider", "")).lower() == "xiaomi":
            k = s.get("key", "")
            KEY = (k[0] if isinstance(k, list) and k else k) or ""
            BASE = s.get("api_base", "") or ""
            break
if not BASE:
    BASE = "https://api.xiaomimimo.com/v1"
if not KEY:
    print("!! 未找到 xiaomi key")
    sys.exit(1)

# ---- 音色方案：(标识, 音色描述, 演示文本) ----
# 目标：高亢尖细、语速快、爆发力强、有金属穿透力的"元气少女"类型声线
# 注意：只做「同类特征」，不复刻特定声优本人的音色
VOICES = [
    ("G_高亢元气",
     "非常年轻的少女声，音调极高且尖锐清亮，音色带着金属质感的穿透力，边缘锋利但不沙哑，"
     "中气充足不虚，语速偏快，咬字利落干脆，句尾上扬带冲劲，情绪外放张扬，"
     "元气满满像随时要跳起来。",
     "喂！你到底在磨蹭什么呀！本小姐可是特意等你的欸！"
     "快点快点，不然我可要生气咯！"),

    ("H_凌厉攻气",
     "少女音但带压迫感，音调高而锐利，音色冷冽有金属光泽，语速又快又有力，"
     "每个字都像是砸出来的，咬字狠且不留气声，尾音收紧不拖沓，"
     "带一丝危险的沙哑边缘，像是不耐烦到极点却又强行克制着的人。",
     "……我说过了，别再让我重复第二遍。"
     "你要是再磨蹭，就别怪我不客气了，听见没有？"),

    ("I_清脆二次元",
     "非常年轻的少女声，音调高亢明亮，音色清脆得像玻璃一样透亮，中气足、穿透力强，"
     "语速轻快活泼，句尾自然上扬带笑，整个人像在发光，"
     "是那种标准的热血少女角色的声音。",
     "好耶！今天也要元气满满地出发啦！"
     "有什么需要我做的尽管说，我可不会输给任何困难哦！"),

    ("J_日语高亢",
     "very young girl voice, extremely high and sharp pitch, crystal clear with metallic "
     "brightness, strong projection, fast paced, crisp articulation, energetic and outgoing, "
     "like a determined anime heroine shouting to get attention.",
     "うわっ、べ、別に怒ってないわよ！ねえ、そこの君、ちょっとうちに来ない？"
     "……ううん、別に待ってたわけじゃないけど！"),
]

LINE = "各位好，我是大肥鱼，今天也请多多关照啦！"


def synth(model, desc, text, tag):
    url = BASE.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": desc},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav"},
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("  HTTP %s  %s" % (e.code, e.read().decode()[:300]))
        return None
    except Exception as e:
        print("  ERR", type(e).__name__, str(e)[:150])
        return None

    msg = (j.get("choices") or [{}])[0].get("message") or {}
    audio = msg.get("audio")
    if isinstance(audio, dict):
        raw = audio.get("data") or audio.get("audio") or ""
    else:
        raw = audio or ""
    if not raw:
        print("  无音频返回，完整响应:", json.dumps(j, ensure_ascii=False)[:400])
        return None
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    path = os.path.join(OUT_DIR, "%s.wav" % tag)
    with open(path, "wb") as f:
        f.write(base64.b64decode(raw))
    size = os.path.getsize(path)
    print("  ✓ %s  %.1f KB  %s" % (tag, size / 1024, path))
    return path


def main():
    model = os.environ.get("MODEL", "mimo-v2.5-tts-voicedesign")
    print("模型: %s" % model)
    print("api_base: %s" % BASE)
    print()
    for tag, desc, text in VOICES:
        print("[%s]" % tag)
        print("  描述: %s" % desc[:60] + "...")
        synth(model, desc, text, tag)
    print()
    print("输出目录: %s" % OUT_DIR)


main()
