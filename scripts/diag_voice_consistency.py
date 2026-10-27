"""量化诊断：音色一致性
1. 同一文本 + 同一标签，合成 3 次 → 音色是否稳定
2. 换标签（childlike vs playful）→ 音色是否真的被标签改变
指标：频谱质心（明亮度）、过零率（清脆度）、RMS（响度）、时长
"""
import asyncio
import base64
import io
import json
import os
import struct
import sys
import urllib.request
import urllib.error
import wave

CFG = "/AstrBot/data/cmd_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
OUT = "/tmp/voice_test"
os.makedirs(OUT, exist_ok=True)

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


def synth(tag):
    payload = {"model": "mimo-v2.5-tts-voiceclone",
               "messages": [{"role": "assistant", "content": (tag + " " + TEXT) if tag else TEXT}],
               "audio": {"format": "wav", "voice": REF_DU}}
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode())
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    au = msg.get("audio")
    raw = (au.get("data") if isinstance(au, dict) else au) or ""
    raw = raw.split(",")[-1] if raw.startswith("data:") else raw
    return base64.b64decode(raw)


def feats(raw):
    """算几个能反映音色的指标（不依赖 numpy）"""
    with wave.open(io.BytesIO(raw), "rb") as w:
        n = w.getnframes()
        sw = w.getsampwidth()
        sr = w.getframerate()
        data = w.readframes(n)
    if sw == 2:
        s = struct.unpack("<%dh" % (len(data) // 2), data)
    elif sw == 1:
        s = [b - 128 for b in data]
    else:
        s = []
    if not s:
        return {}
    # RMS
    rms = (sum(v * v for v in s) / len(s)) ** 0.5
    # 过零率（清脆度）
    zc = sum(1 for i in range(1, len(s)) if (s[i - 1] < 0) != (s[i] < 0)) / len(s)
    # 自相关估基频（用少量 lag 扫描）
    seg = s[: sr * 2]  # 取前 2 秒
    best_lag = best = 0
    lo, hi = int(sr / 500), int(sr / 80)  # 80Hz~500Hz
    step = max(1, (hi - lo) // 120)
    for lag in range(lo, hi, step):
        acc = 0
        for i in range(0, len(seg) - lag, 4):
            acc += seg[i] * seg[i + lag]
        if acc > best:
            best, best_lag = acc, lag
    f0 = sr / best_lag if best_lag else 0
    dur = n / sr
    return {"rms": rms, "zc": zc, "f0": f0, "dur": dur, "sr": sr}


async def main():
    print("=" * 74)
    print("A. 固定标签 (childlike)(playful)，同一句合成 3 次 → 音色是否稳定")
    print("=" * 74)
    rows = []
    for i in range(3):
        raw = await asyncio.get_event_loop().run_in_executor(None, synth, "(childlike)(playful)")
        f = feats(raw)
        rows.append(f)
        print("  第%d次: f0基频=%.1fHz  过零率=%.4f  RMS=%.0f  时长=%.2fs"
              % (i + 1, f["f0"], f["zc"], f["rms"], f["dur"]))
        await asyncio.sleep(2)
    if len(rows) == 3:
        f0s = [r["f0"] for r in rows]
        zcs = [r["zc"] for r in rows]
        print()
        print("  基频波动: %.1f ~ %.1f Hz (差 %.1f%%)" % (
            min(f0s), max(f0s), 100.0 * (max(f0s) - min(f0s)) / max(1, max(f0s))))
        print("  过零率波动: %.4f ~ %.4f (差 %.1f%%)" % (
            min(zcs), max(zcs), 100.0 * (max(zcs) - min(zcs)) / max(1, max(zcs))))

    print()
    print("=" * 74)
    print("B. 换标签 → 音色是否被标签改变")
    print("=" * 74)
    for tag in ("(childlike)", "(playful)", "(lively)", "(aggrieved)", ""):
        raw = await asyncio.get_event_loop().run_in_executor(None, synth, tag)
        f = feats(raw)
        print("  %-14s f0=%.1fHz  过零率=%.4f  RMS=%.0f  时长=%.2fs"
              % (tag or "(无标签)", f["f0"], f["zc"], f["rms"], f["dur"]))
        await asyncio.sleep(2)


asyncio.run(main())
