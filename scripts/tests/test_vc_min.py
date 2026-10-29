"""voiceclone 传参验证（已确认 audio.voice 必须是 DataURL）"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

CFG = "/AstrBot/data/cmd_config.json"
REF = os.environ.get("REF_AUDIO", "/AstrBot/data/plugin_data/mimo_tts_samples/T_元气傲娇.wav")
OUT = "/AstrBot/data/plugin_data/mimo_tts_samples"
TEXT = os.environ.get("TEXT", "哼，本小姐出手还不是手到擒来！你呀，就乖乖等着听好消息吧——才、才不是想让你夸我呢！")

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


def try_one(label, payload):
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("  %-22s HTTP %s  %s" % (label, e.code, e.read().decode()[:200].replace("\n", " ")))
        return False
    except Exception as e:
        print("  %-22s ERR %s" % (label, type(e).__name__))
        return False
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    audio = msg.get("audio")
    raw = (audio.get("data") if isinstance(audio, dict) else audio) or ""
    if not raw:
        print("  %-22s 无音频 %s" % (label, json.dumps(j, ensure_ascii=False)[:200]))
        return False
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    path = os.path.join(OUT, "clone_%s.wav" % label)
    with open(path, "wb") as f:
        f.write(base64.b64decode(raw))
    print("  %-22s ✓ %.1f KB  %s" % (label, os.path.getsize(path) / 1024, os.path.basename(path)))
    return True


print("参考: %s" % os.path.basename(REF))
print("文本: %s\n" % TEXT)

# B1：audio.voice=DataURL，messages 只有 assistant 文本
try_one("B1_voice_only", {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [{"role": "assistant", "content": TEXT}],
    "audio": {"format": "wav", "voice": ref_dataurl},
})
time.sleep(8)

# B3：audio.voice=DataURL + user 消息是风格指令
try_one("B3_voice_and_style", {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [
        {"role": "user", "content": "用元气又傲娇、带点娇嗔的语气说，像在跟人斗嘴。"},
        {"role": "assistant", "content": TEXT},
    ],
    "audio": {"format": "wav", "voice": ref_dataurl},
})
