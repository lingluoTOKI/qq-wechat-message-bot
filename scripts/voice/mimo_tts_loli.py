"""真正的「幼态萝莉」音色探索
关键差异：钉宫型 = 成年声线演少女（锐利有力、爆发力强）
          萝莉型 = 真正幼态童声（头腔共鸣、奶音、音色小而集中、气息浅）
重点描述维度：年龄感 / 头腔共鸣 / 奶音鼻音 / 音量小气息浅 / 音节短促跳跃
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
    ("N_童声萝莉",
     "非常幼态的小女孩声，听起来只有八九岁，音调极高，音色小而集中，"
     "主要用头腔共鸣发声，带明显的童声奶音和一点轻微鼻音，"
     "音量很小、气息很浅，能听见换气声，语速偏快，音节短促跳跃，"
     "每个字都清脆得像小铃铛，句尾轻快地扬起，天真活泼又带点不讲道理的娇气。",
     "哇！好耶好耶！大肥鱼现在就去给你做好！"
     "……不过、不过你可要记得夸我哦！不然我就、我就生气啦！"),

    ("O_元气萝莉",
     "很年轻的女孩声，年龄感十一二岁，音调高而明亮，音色清脆偏细，"
     "用上半声区和头腔共鸣发声，带一点点奶音，音量不大但很清亮，"
     "语速轻快，节奏跳动感强，句尾利落上扬，"
     "整个人像小太阳一样元气外放，热情又有活力，像在抢着说话。",
     "喂喂喂！听到没有！本小姐——啊不对，我可是大肥鱼诶！"
     "有什么需要尽管说，交给我啦！"),

    ("P_英文童声",
     "a very young childlike girl voice, about eight or nine years old, extremely high pitch, "
     "small focused timbre, strongly head-chest voice dominant with a cute babyish nasal quality, "
     "very soft volume and shallow breath, audible small breaths between phrases, "
     "fast speech with short bouncy syllables, bell-like crisp ending on each word, "
     "childish and playful with a hint of bratty stubbornness",
     "Hmph! Fine, I'll do it for you! But you have to say thank you, okay? "
     "Or else... I'll get a little angry, hehe."),

    ("Q_幼态傲娇腹黑",
     "非常幼态的小女孩声，听起来只有八九岁，音调极高，音色小而集中，"
     "头腔共鸣为主，带着软软的奶音和轻微鼻音，音量小、气息浅，"
     "语速偏快、音节短促，但语气却故作老练地傲娇，"
     "嘴上说着不服气的话，尾音黏黏地上扬带轻哼，"
     "甜软的表面下藏着一丝小大人的狡黠，像披着糖衣的小坏蛋在偷笑。",
     "哼，本小姐才不是因为想你才主动找你的呢！"
     "……骗你的啦，我就是在想你了，怎样？"),
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
