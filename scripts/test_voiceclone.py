"""验证 voiceclone 能否用「voicedesign 生成的音频」当参考音频固化音色
参考音频 = 我方自己生成的 J_日语高亢.wav（虚构音色，零真人声纹风险）
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

CFG = "/AstrBot/data/cmd_config.json"
REF = os.environ.get("REF_AUDIO", "/AstrBot/data/plugin_data/mimo_tts_samples/J_日语高亢.wav")
OUT = os.environ.get("OUT_DIR", "/AstrBot/data/plugin_data/mimo_tts_samples")
TEXT = os.environ.get("TEXT", "今日の天気はいいわね。べ、別に嬉しくなっちゃったわけじゃないけど！")

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
    ref_raw = f.read()
ref_b64 = base64.b64encode(ref_raw).decode()
print("参考音频: %s  (%.1f KB)" % (os.path.basename(REF), len(ref_raw) / 1024))
print("api_base: %s" % BASE)
print("测试文本: %s" % TEXT)
print()

HEADERS = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}


def post(url, payload, label):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:260]
        print("  %-34s HTTP %s  %s" % (label, e.code, body.replace("\n", " ")))
        return None
    except Exception as e:
        print("  %-34s ERR %s" % (label, type(e).__name__))
        return None
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    audio = msg.get("audio")
    raw = (audio.get("data") if isinstance(audio, dict) else audio) or ""
    if not raw:
        print("  %-34s HTTP200 但无音频: %s" % (label, json.dumps(j, ensure_ascii=False)[:220]))
        return None
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    path = os.path.join(OUT, "clone_%s.wav" % label.replace("/", "_").replace(" ", ""))
    with open(path, "wb") as f:
        f.write(base64.b64decode(raw))
    print("  %-34s ✓ %.1f KB -> %s" % (label, os.path.getsize(path) / 1024, os.path.basename(path)))
    return path


url = BASE.rstrip("/") + "/chat/completions"

print("方案 A：chat/completions + user 消息带 input_audio")
post(url, {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [
        {"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": "data:audio/wav;base64," + ref_b64}}]},
        {"role": "assistant", "content": TEXT},
    ],
    "audio": {"format": "wav"},
}, "A_input_audio")

print()
print("方案 B：chat/completions + audio.voice 传 base64")
post(url, {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [
        {"role": "assistant", "content": TEXT},
    ],
    "audio": {"format": "wav", "voice": ref_b64},
}, "B_audio_voice")

print()
print("方案 C：chat/completions + reference_audio 字段")
post(url, {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [{"role": "assistant", "content": TEXT}],
    "audio": {"format": "wav"},
    "reference_audio": ref_b64,
}, "C_reference_audio")

print()
print("方案 D：/audio/speech + extra_body")
try:
    req = urllib.request.Request(
        BASE.rstrip("/") + "/audio/speech",
        data=json.dumps({"model": "mimo-v2.5-tts-voiceclone", "voice": "mimo_default",
                         "input": TEXT, "reference_audio": ref_b64}, ensure_ascii=False).encode(),
        headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    path = os.path.join(OUT, "clone_D_speech.wav")
    with open(path, "wb") as f:
        f.write(data)
    print("  %-34s ✓ %.1f KB" % ("D_audio_speech", len(data) / 1024))
except urllib.error.HTTPError as e:
    print("  %-34s HTTP %s  %s" % ("D_audio_speech", e.code, e.read().decode()[:220].replace("\n", " ")))
except Exception as e:
    print("  %-34s ERR %s" % ("D_audio_speech", type(e).__name__))
