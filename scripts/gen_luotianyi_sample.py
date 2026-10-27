"""用洛天依原声参考音频生成样音 + 测一致性
A. 三句不同内容（模拟分句连发）→ 给人耳验收音色
B. 同一句合成 3 次 → LTAS 相似度（一致性指标）
"""
import asyncio
import base64
import hashlib
import io
import json
import urllib.request
import wave

import numpy as np

CFG = "/AstrBot/data/cmd_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_luotianyi.wav"
OUT = "/AstrBot/data/plugin_data/mimo_tts_samples"

d = json.load(open(CFG, encoding="utf-8-sig"))
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
    REF_DU = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()

URL = BASE.rstrip("/") + "/chat/completions"
HEADERS = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}


def synth(text, voice_tag="(childlike)", emo_tag="(playful)"):
    tag = voice_tag + emo_tag
    payload = {"model": "mimo-v2.5-tts-voiceclone",
               "messages": [{"role": "assistant", "content": tag + " " + text}],
               "audio": {"format": "wav", "voice": REF_DU},
               "seed": 42, "temperature": 0}
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode())
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    au = msg.get("audio")
    raw = (au.get("data") if isinstance(au, dict) else au) or ""
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    return base64.b64decode(raw)


def ltas(raw):
    with wave.open(io.BytesIO(raw), "rb") as w:
        data = w.readframes(w.getnframes())
    s = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    n_fft, hop = 1024, 512
    win = np.hanning(n_fft).astype(np.float32)
    frames = [s[i:i + n_fft] * win for i in range(0, max(0, len(s) - n_fft), hop)]
    if not frames:
        return np.zeros(1)
    return np.abs(np.fft.rfft(np.array(frames), axis=1)).mean(axis=0)


def cos(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


CASES = [
    ("哼，本小姐今天心情不错，勉为其难夸你一句吧！", "(aggrieved)"),
    ("你呀，就乖乖等着听好消息好了～", "(playful)"),
    ("才、才不是想让你夸我呢！", "(aggrieved)"),
]


async def main():
    print("=" * 70)
    print("A. 洛天依参考音频 · 三句样音（模拟分句连发）")
    print("=" * 70)
    for i, (text, emo) in enumerate(CASES, 1):
        raw = await asyncio.get_event_loop().run_in_executor(None, synth, text, "(childlike)", emo)
        path = "%s/luotianyi_s%d.wav" % (OUT, i)
        with open(path, "wb") as f:
            f.write(raw)
        print("  第%d句 %-30s 标签 (childlike)%s  → %.0f KB"
              % (i, text[:14] + "…", emo, len(raw) / 1024))
        await asyncio.sleep(1)

    print()
    print("=" * 70)
    print("B. 同一句合成 3 次 → 一致性")
    print("=" * 70)
    specs = []
    for i in range(3):
        raw = await asyncio.get_event_loop().run_in_executor(
            None, synth, "哼，本小姐出手还不是手到擒来！", "(childlike)", "(aggrieved)")
        specs.append(ltas(raw))
        print("  第%d次 md5=%s" % (i + 1, hashlib.md5(raw).hexdigest()[:12]))
        await asyncio.sleep(1)
    sims = [cos(specs[i], specs[j]) for i in range(3) for j in range(i + 1, 3)]
    print("  LTAS 相似度 平均 %.4f（最低 %.4f）" % (sum(sims) / len(sims), min(sims)))
    print("  （对比旧参考音频：平均 0.8701）")


asyncio.run(main())
