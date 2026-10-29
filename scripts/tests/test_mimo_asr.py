"""端到端实测 MiMo ASR：edge-tts 合成中文语音 -> MiMo mimo-v2.5-asr 转写
背景：用户曾反馈群里语音回复出现「4″ [呃，什么都没有听到]」，疑似 ASR 返回空。
"""
import asyncio
import base64
import json
import os
import sys

# 从 cmd_config.json 取 xiaomi 的 key / base
CFG = "/AstrBot/data/cmd_config.json"
d = json.load(open(CFG, encoding="utf-8"))
KEY = ""
BASE = ""
STT_MODEL = "mimo-v2.5-asr"
for s in d.get("provider_sources", []):
    if s.get("type") == "mimo_stt_api" or s.get("id") == "xiaomi_stt":
        KEY = s.get("api_key", "") or KEY
        BASE = s.get("api_base", "") or BASE
        break
if not KEY:
    for s in d.get("provider_sources", []):
        if str(s.get("provider", "")).lower() == "xiaomi":
            k = s.get("key", "")
            if isinstance(k, list):
                k = k[0] if k else ""
            KEY = k or KEY
            BASE = s.get("api_base", "") or BASE
            break
if not BASE:
    BASE = "https://api.xiaomimimo.com/v1"
if not KEY:
    print("!! 未找到 xiaomi api_key")
    sys.exit(1)
print("api_base =", BASE)
print("key 长度 =", len(KEY))

import edge_tts

AUDIO = "/tmp/asr_test.mp3"
TEXT = "今天也要加油哦，语音识别测试"


async def synth():
    c = edge_tts.Communicate(TEXT, "zh-CN-XiaoyiNeural")
    with open(AUDIO, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
    return os.path.getsize(AUDIO)


async def asr():
    import aiohttp
    with open(AUDIO, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode()
    payload = {
        "model": STT_MODEL,
        "messages": [{
            "role": "user",
            "content": [{"type": "input_audio",
                         "input_audio": {"data": "data:audio/mpeg;base64," + b64}}],
        }],
    }
    url = BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=40)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=payload, headers=headers) as r:
            txt = await r.text()
            print("HTTP", r.status)
            print("响应:", txt[:700])
            if r.status == 200:
                try:
                    j = json.loads(txt)
                    content = j["choices"][0]["message"]["content"]
                    print()
                    print("=" * 50)
                    print("识别结果: %r" % content)
                    print("原文    : %r" % TEXT)
                    print("=" * 50)
                    return content
                except Exception as e:
                    print("解析失败:", e)
    return None


async def main():
    size = await synth()
    print("合成音频: %d 字节 (%.1f 秒 @ 24kHz mp3 约 %.1fs)" % (
        size, size / 1024, size / 24000 / 1024 * 1.0))
    dur = size / 24000 / 1024
    print("理论成本: ¥%.6f (按 ¥0.5/小时)" % (dur / 3600 * 0.5))
    print()
    await asr()


asyncio.run(main())
