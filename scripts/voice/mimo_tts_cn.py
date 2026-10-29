"""J（日语高亢）的中文版：对比「中语音色」与「日语音色」的差异
用法: MODEL=... VOICES=K python3 mimo_tts_try.py
"""
import base64
import json
import os
import sys
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
    # K：J 的英文描述直译成中文，台词也中文
    ("K_中文同款",
     "非常年轻的少女声，音调极高且尖锐，清澈透明并带有金属质感，中气充足、穿透力强，"
     "语速偏快，咬字利落，元气外放，像决心满满的角色在喊话吸引注意。",
     "今天的天气真不错呢。哼，我、我才没有因为能跟你说话就开心呢！"
     "……才、才不是故意的！"),

    # L：中文描述里补上「日语动画角色」的节奏特征，看看能不能做出 J 的味道
    ("L_中文带日式",
     "非常年轻的少女声，音调极高且尖锐，清脆透明带金属光泽，中气足、穿透力强。"
     "语速偏快，音调起伏幅度大，句尾利落上扬，情绪外放爽朗，"
     "整体是热血动画里女主角喊话时的感觉，元气十足、带一点不服输的冲劲。",
     "喂！你到底在磨蹭什么呀！本小姐可是特意等你的欸！"
     "快点快点，不然我可要生气咯！"),

    # M：中文里点明「轻小说角色」的气声与跳动感，介于 D/F 的软萌与 G 的高亢之间
    ("M_中文高挑",
     "很年轻的女孩嗓音，音调偏高而尖，音色清亮有金属感，音量不大但穿透力强，"
     "语速轻快，咬字清楚利落，尾音干脆上扬不拖沓，"
     "带一点少女特有的娇气与不服输，整体明亮有活力，像在跟人拌嘴又像在撒娇。",
     "哼，答应我一件事嘛——下次要早点来找我哦。"
     "……不可以哦？哼，那我就一直缠着你好了！"),
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
print("模型: %s" % model)
for tag, desc, text in VOICES:
    print("[%s]" % tag)
    synth(model, desc, text, tag)
    import time
    time.sleep(4)
print("输出: %s" % OUT_DIR)
