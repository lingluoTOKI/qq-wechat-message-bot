#!/usr/bin/env python3
"""本地实测：加载洛天依 v2Pro 微调权重并合成音频。

验证三件事：
1. set_gpt_weights / set_sovits_weights 能否加载我们这份权重
2. v2Pro 的推理耗时（GPU）
3. 输出是否为合法 WAV
"""
import json
import os
import time
import urllib.parse
import urllib.request

API = "http://127.0.0.1:9880"
ROOT = r"F:\github\GPT-SoVITS-v2pro-20250604"
GPT = os.path.join(ROOT, "GPT_weights_v2Pro", "luotianyi13_e15.ckpt")
VITS = os.path.join(ROOT, "SoVITS_weights_v2Pro", "luotianyi13_e8_s384.pth")
REF = os.path.join(ROOT, "refs", "luotianyi13_ref.wav")
PROMPT_TEXT = "我现在需要一个长达三秒的语音"
OUT_DIR = r"F:\github\BOT用\voicetest\lty"

TEXTS = [
    ("哼，本小姐出手还不是手到擒来！你呀，就乖乖等着听好消息吧。", "s1_aoyao.wav"),
    ("这破游戏又卡了？我超，你先别急，我看看服务器是不是炸了。", "s2_richang.wav"),
    ("呜……你终于来啦，大肥鱼等你好久了哦。", "s3_ruanmeng.wav"),
]


def get(path, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.status, r.read()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== 1. 加载微调权重 ===")
    t0 = time.time()
    try:
        st, body = get("/set_gpt_weights", {"weights_path": GPT})
        print("  set_gpt_weights   HTTP %s  %.1fs  %s" % (st, time.time() - t0, body[:120]))
    except Exception as e:
        print("  set_gpt_weights 失败:", type(e).__name__, str(e)[:200])

    t0 = time.time()
    try:
        st, body = get("/set_sovits_weights", {"weights_path": VITS})
        print("  set_sovits_weights HTTP %s  %.1fs  %s" % (st, time.time() - t0, body[:120]))
    except Exception as e:
        print("  set_sovits_weights 失败:", type(e).__name__, str(e)[:200])

    print()
    print("=== 2. 合成 ===")
    for text, name in TEXTS:
        t0 = time.time()
        try:
            st, body = get("/tts", {
                "text": text,
                "text_lang": "zh",
                "ref_audio_path": REF,
                "prompt_text": PROMPT_TEXT,
                "prompt_lang": "zh",
                "media_type": "wav",
                "streaming_mode": "false",
                "speed_factor": 1.0,
            })
            dt = time.time() - t0
            if st != 200:
                print("  %-16s HTTP %s  %s" % (name, st, body[:200]))
                continue
            if body[:4] != b"RIFF":
                try:
                    j = json.loads(body)
                    print("  %-16s 非音频返回: %s" % (name, str(j)[:200]))
                except Exception:
                    print("  %-16s 非 WAV 内容: %r" % (name, body[:80]))
                continue
            p = os.path.join(OUT_DIR, name)
            with open(p, "wb") as f:
                f.write(body)
            # 时长
            import wave
            w = wave.open(p, "rb")
            dur = w.getnframes() / w.getframerate()
            sr = w.getframerate()
            w.close()
            print("  %-16s OK  %.1fKB  %.2fs  %dHz  合成耗时%.2fs  (RTF %.2f)"
                  % (name, len(body) / 1024, dur, sr, dt, dt / dur if dur else 0))
        except Exception as e:
            print("  %-16s 异常: %s" % (name, type(e).__name__ + ": " + str(e)[:160]))

    print()
    print("输出目录:", OUT_DIR)


if __name__ == "__main__":
    main()
