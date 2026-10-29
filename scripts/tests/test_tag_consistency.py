"""决定性验证：标签维度拆分能否消除「每次音色不一样」
对比：
  A 修复后 —— 音色标签固定 (childlike)，情绪标签按句变化（不改音色）
  B 修复前 —— 音色标签按句变化（childlike / playful / lively …）
指标：长时平均功率谱(LTAS)余弦相似度，跨句子比较
"""
import asyncio
import base64
import io
import json
import os
import sys
import types
import shutil
import tempfile
import urllib.request
import wave

import numpy as np

CFG = "/AstrBot/data/cmd_config.json"
REF = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
SRC = os.environ.get("CLONETTS_SRC", "/AstrBot/data/plugins/astrbot_plugin_clonetts")


def _mod(name):
    x = types.ModuleType(name)
    sys.modules[name] = x
    return x


a = _mod("astrbot"); a.__path__ = []
api = _mod("astrbot.api"); a.api = api


class L:
    def debug(s, *x, **k): pass
    info = warning = error = debug


api.logger = L()
st = _mod("astrbot.api.star")
st.Context = type("C", (), {})
st.Star = type("S", (), {"__init__": lambda s, c: None})
st.register = lambda *x, **k: (lambda f: f)
st.StarTools = type("T", (), {"get_data_dir": staticmethod(lambda n: "/tmp")})
api.star = st
ev = _mod("astrbot.api.event")
ev.filter = type("F", (), {
    "EventMessageType": type("E", (), {"ALL": 1}),
    "command": staticmethod(lambda *x, **k: (lambda f: f)),
    "event_message_type": staticmethod(lambda *x, **k: (lambda f: f)),
    "llm_tool": staticmethod(lambda *x, **k: (lambda f: f)),
    "on_llm_request": staticmethod(lambda *x, **k: (lambda f: f)),
    "on_llm_response": staticmethod(lambda *x, **k: (lambda f: f)),
    "on_decorating_result": staticmethod(lambda *x, **k: (lambda f: f)),
})()
ev.AstrMessageEvent = type("A", (), {})
ev.MessageChain = type("M", (), {"__init__": lambda s, c=None: None})
api.event = ev
mc = _mod("astrbot.api.message_components")
mc.Plain = type("P", (), {"__init__": lambda s, t: None})
mc.Record = type("R", (), {"fromBase64": staticmethod(lambda b: ("REC", b))})
api.message_components = mc
core = _mod("astrbot.core"); core.__path__ = []
mcore = _mod("astrbot.core.agent"); mcore.__path__ = []
rc = _mod("astrbot.core.agent.run_context")
rc.ContextWrapper = type("CW", (), {"__class_getitem__": classmethod(lambda c, i: c)})
tl = _mod("astrbot.core.agent.tool")
tl.FunctionTool = type("FT", (), {"__class_getitem__": classmethod(lambda c, i: c)})
tl.ToolExecResult = type("TER", (), {})
ac = _mod("astrbot.core.astr_agent_context")
ac.AstrAgentContext = type("AC", (), {})
cfg = _mod("astrbot.core.config")
cfg.AstrBotConfig = type("ABC", (), {})
ent = _mod("astrbot.core.provider.entities")
ent.LLMResponse = type("LR", (), {})

PKG = "ct_tag_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
shutil.copytree(SRC, D)
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
ttsmod = __import__(PKG + ".tts_api.dy_tts_api", fromlist=["dy_tts_api"])

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


class FakeSelf:
    voice_type = "mimo-v2.5-tts-voiceclone"
    mimo_tts_ref_audio = REF
    mimo_tts_api_base = ""
    mimo_tts_api_key = ""
    mimo_tts_voice_desc = ""
    mimo_tts_style_prompt = ""
    mimo_tts_default_tag = ""
    mimo_tts_voice_tag = ""
    mimo_tts_emotion_tag = ""
    mimo_tts_seed = 42
    mimo_tts_temperature = 0
    fallback_voice_type = "zh-CN-XiaoyiNeural"
    speed_ratio = 0
    edge_pitch = "+15Hz"
    api_key = ""


def synth_raw(tag, text):
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
        sr = w.getframerate()
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


# 三种情绪场景（修复前会得到不同音色标签，修复后音色标签固定）
CASES = [
    ("哼，谁要你夸了！", "aggrieved", "childlike"),
    ("你到底要说什么呀？", "playful", "playful"),
    ("太好了！", "lively", "lively"),
]


async def main():
    print("=" * 78)
    print("A. 修复后：音色标签固定 (childlike)，情绪标签按句变化")
    print("=" * 78)
    specs_new, used = [], []
    for text, emo, _old in CASES:
        tag = "(childlike)" + "(%s)" % emo
        raw = await asyncio.get_event_loop().run_in_executor(None, lambda: synth_raw(tag, text))
        specs_new.append(ltas(raw))
        used.append(tag)
        print("  %-14s 标签 %s" % (text[:12], tag))
        await asyncio.sleep(2)
    sims_new = [cos(specs_new[i], specs_new[j]) for i in range(3) for j in range(i + 1, 3)]
    avg_new = sum(sims_new) / len(sims_new)
    print("  → 跨句子音色相似度 平均 %.4f（最低 %.4f）" % (avg_new, min(sims_new)))

    print()
    print("=" * 78)
    print("B. 修复前：音色标签按句变化（childlike / playful / lively）")
    print("=" * 78)
    specs_old = []
    for text, _emo, old_tag in CASES:
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: synth_raw("(%s)" % old_tag, text))
        specs_old.append(ltas(raw))
        print("  %-14s 标签 (%s)" % (text[:12], old_tag))
        await asyncio.sleep(2)
    sims_old = [cos(specs_old[i], specs_old[j]) for i in range(3) for j in range(i + 1, 3)]
    avg_old = sum(sims_old) / len(sims_old)
    print("  → 跨句子音色相似度 平均 %.4f（最低 %.4f）" % (avg_old, min(sims_old)))

    print()
    print("=" * 78)
    print("结论：跨句子音色一致性 %.4f → %.4f（%+.1f%%）"
          % (avg_old, avg_new, (avg_new - avg_old) / avg_old * 100))
    print("      最低相似度 %.4f → %.4f" % (min(sims_old), min(sims_new)))
    print("=" * 78)


asyncio.run(main())
