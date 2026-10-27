"""严谨诊断音色一致性
1. 完全相同的请求发多次 → 输出是否字节级相同（确定性）
2. 若不同，用长时平均频谱(LTAS)余弦相似度衡量音色差异（不受语调影响）
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import urllib.request
import wave

import numpy as np

CFG = "/AstrBot/data/cmd_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"

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
TEXT = "哼，本小姐出手还不是手到擒来！"
TAG = "(childlike)(playful)"


def synth(tag, seed=None, temp=None, text=TEXT):
    payload = {"model": "mimo-v2.5-tts-voiceclone",
               "messages": [{"role": "assistant", "content": tag + " " + text}],
               "audio": {"format": "wav", "voice": REF_DU}}
    if seed is not None:
        payload["seed"] = seed
    if temp is not None:
        payload["temperature"] = temp
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
    """长时平均功率谱：刻画音色轮廓，对音高/语调不敏感"""
    with wave.open(io.BytesIO(raw), "rb") as w:
        sr = w.getframerate()
        data = w.readframes(w.getnframes())
    s = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if len(s) < 2048:
        return np.zeros(1), sr
    n_fft, hop = 1024, 512
    win = np.hanning(n_fft).astype(np.float32)
    frames = [s[i:i + n_fft] * win for i in range(0, len(s) - n_fft, hop)]
    if not frames:
        return np.zeros(1), sr
    sp = np.abs(np.fft.rfft(np.array(frames), axis=1)).mean(axis=0)
    return sp, sr


def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


async def group(name, seed, temp, n=3):
    raws = []
    for _ in range(n):
        raws.append(await asyncio.get_event_loop().run_in_executor(
            None, lambda: synth(TAG, seed, temp)))
        await asyncio.sleep(2)
    hashes = [hashlib.md5(r).hexdigest()[:10] for r in raws]
    same = len(set(hashes)) == 1
    specs = [ltas(r) for r in raws]
    sims = []
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            sims.append(cosine(specs[i][0], specs[j][0]))
    print("  %-20s 字节哈希 %s %s" % (name, " ".join(hashes), "← 完全相同!" if same else ""))
    if sims:
        print("  %-20s 音色谱相似度 %.4f (最高 %.4f 最低 %.4f)" % ("", sum(sims) / len(sims), max(sims), min(sims)))
    return same, (sum(sims) / len(sims) if sims else 0)


async def main():
    print("=" * 76)
    print("A. 带 seed=42 + temperature=0（插件现在的做法）")
    print("=" * 76)
    a = await group("seed+temp0", 42, 0)
    print()
    print("=" * 76)
    print("B. 不带 seed/temperature（旧行为）")
    print("=" * 76)
    b = await group("无参数", None, None)
    print()
    print("=" * 76)
    print("C. 同一 seed 重复请求，看是否字节级确定性")
    print("=" * 76)
    c = await group("seed=42 再来3次", 42, 0)
    print()
    print("=" * 76)
    print("结论：")
    print("  带参数 字节完全相同 = %s，音色相似度 %.4f" % (a[0], a[1]))
    print("  不带参 字节完全相同 = %s，音色相似度 %.4f" % (b[0], b[1]))
    print("=" * 76)


asyncio.run(main())
