"""验证能否让 MiMo TTS 输出稳定
思路：生成式模型每次都有随机性，若 API 支持 temperature=0 / seed，就能锁死
测量：只取前 0.8 秒算基频（避开语调起伏影响），对比组内波动
"""
import asyncio
import base64
import io
import json
import os
import struct
import urllib.request
import wave

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


def synth(extra_top=None, extra_audio=None):
    payload = {"model": "mimo-v2.5-tts-voiceclone",
               "messages": [{"role": "assistant", "content": TAG + " " + TEXT}],
               "audio": {"format": "wav", "voice": REF_DU}}
    if extra_audio:
        payload["audio"].update(extra_audio)
    if extra_top:
        payload.update(extra_top)
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode())
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    au = msg.get("audio")
    raw = (au.get("data") if isinstance(au, dict) else au) or ""
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    return base64.b64decode(raw)


def f0_head(raw, seconds=0.8):
    """只取开头一小段算基频，避开语调起伏"""
    with wave.open(io.BytesIO(raw), "rb") as w:
        sr = w.getframerate()
        sw = w.getsampwidth()
        n = min(w.getnframes(), int(sr * seconds))
        data = w.readframes(n)
    s = struct.unpack("<%dh" % (len(data) // 2), data) if sw == 2 else []
    if len(s) < 1000:
        return 0.0
    best_lag = best = 0
    lo, hi = int(sr / 500), int(sr / 80)
    step = max(1, (hi - lo) // 200)
    for lag in range(lo, hi, step):
        acc = 0
        for i in range(0, len(s) - lag, 3):
            acc += s[i] * s[i + lag]
        if acc > best:
            best, best_lag = acc, lag
    return sr / best_lag if best_lag else 0.0


def zc_all(raw):
    with wave.open(io.BytesIO(raw), "rb") as w:
        sw = w.getsampwidth()
        data = w.readframes(w.getnframes())
    s = struct.unpack("<%dh" % (len(data) // 2), data) if sw == 2 else []
    if not s:
        return 0.0
    return sum(1 for i in range(1, len(s)) if (s[i - 1] < 0) != (s[i] < 0)) / len(s)


GROUPS = [
    ("默认", None, None),
    ("temperature=0", {"temperature": 0}, None),
    ("seed=42", {"seed": 42}, None),
    ("temp=0+seed=42", {"temperature": 0, "seed": 42}, None),
    ("audio.temperature=0", None, {"temperature": 0}),
]


async def main():
    print("=" * 76)
    print("各参数组：同一句合成 2 次，看组内基频/过零率波动")
    print("=" * 76)
    for name, top, aud in GROUPS:
        f0s, zcs = [], []
        for _ in range(2):
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: synth(top, aud))
            except Exception as e:
                print("  %-20s 失败: %s" % (name, type(e).__name__))
                break
            f0s.append(f0_head(raw))
            zcs.append(zc_all(raw))
            await asyncio.sleep(2)
        if len(f0s) == 2:
            df = abs(f0s[0] - f0s[1]) / max(1.0, max(f0s)) * 100
            dz = abs(zcs[0] - zcs[1]) / max(1e-6, max(zcs)) * 100
            print("  %-20s 基频 %.0f/%.0f Hz (差 %.1f%%)  过零率 %.4f/%.4f (差 %.1f%%)"
                  % (name, f0s[0], f0s[1], df, zcs[0], zcs[1], dz))


asyncio.run(main())
