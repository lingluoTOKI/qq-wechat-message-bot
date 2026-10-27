"""验证 MiMo TTS 音频标签能否叠加到已固化的克隆音色上
标签写在文本最前面，官方文档说明：所有模型都支持
"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

CFG = "/AstrBot/data/cmd_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
OUT = "/AstrBot/data/plugin_data/mimo_tts_samples"

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
with open(REF, "rb") as f:
    ref_dataurl = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()

HEADERS = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}
URL = BASE.rstrip("/") + "/chat/completions"

CASES = [
    ("V1_克隆+童声俏皮", "mimo-v2.5-tts-voiceclone",
     "(childlike)(playful)哼，本小姐今天心情不错，勉为其难夸你一句吧！"),
    ("V2_克隆+多层标签", "mimo-v2.5-tts-voiceclone",
     "(childlike)(squeaky)(playful)(lively)喂！你到底在磨蹭什么呀，本小姐可是特意等你的欸！"),
    ("V3_克隆+傲娇标签", "mimo-v2.5-tts-voiceclone",
     "(childlike)(playful)(aggrieved)哼，谁、谁要你夸了！我只是刚好路过而已！"),
    ("V4_设计+标签", "mimo-v2.5-tts-voicedesign",
     "(childlike)(playful)(squeaky)喂！你到底在磨蹭什么呀，本小姐可是特意等你的欸！"),
]


def run(tag, model, text, use_ref):
    payload = {"model": model, "messages": [{"role": "assistant", "content": text}],
               "audio": {"format": "wav"}}
    if use_ref:
        payload["audio"]["voice"] = ref_dataurl
    else:
        payload["messages"].insert(0, {"role": "user", "content": (
            "十一二岁的女孩声，音调极高又清脆，带一点奶音但声音结实有力、"
            "中气足有穿透力，语速轻快有跳跃感，元气外放却故作老练地傲娇，"
            "带一点挑衅和娇嗔，尾音黏黏上扬，甜而不软吵而不凶。")})
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("  HTTP %s  %s" % (e.code, e.read().decode()[:180].replace("\n", " ")))
        return False
    except Exception as e:
        print("  ERR", type(e).__name__)
        return False
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    au = msg.get("audio")
    raw = (au.get("data") if isinstance(au, dict) else au) or ""
    if not raw:
        print("  无音频:", json.dumps(j, ensure_ascii=False)[:200])
        return False
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    path = os.path.join(OUT, "%s.wav" % tag)
    with open(path, "wb") as f:
        f.write(base64.b64decode(raw))
    print("  ✓ %-18s %.1f KB" % (tag, os.path.getsize(path) / 1024))
    return True


for tag, model, text in CASES:
    print("[%s]" % tag)
    run(tag, model, text, use_ref=("voiceclone" in model))
    time.sleep(6)
print("\n输出: %s" % OUT)
