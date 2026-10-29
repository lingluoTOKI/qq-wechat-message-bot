"""验证音色稳定性修复：通过插件实际代码路径重复合成，量化波动
对比组：
  A 新默认（seed=42 + temperature=0 + 音色标签固定）
  B 旧行为（不传 seed、不传 temperature、情绪标签按句变化）
"""
import asyncio
import base64
import io
import json
import os
import struct
import sys
import types
import shutil
import tempfile
import wave

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

PKG = "ct_stable_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
shutil.copytree(SRC, D)
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
ttsmod = __import__(PKG + ".tts_api.dy_tts_api", fromlist=["dy_tts_api"])


class FakeSelf:
    voice_type = "mimo-v2.5-tts-voiceclone"
    mimo_tts_ref_audio = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
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


def f0_head(raw, seconds=0.8):
    with wave.open(io.BytesIO(raw), "rb") as w:
        sr = w.getframerate()
        n = min(w.getnframes(), int(sr * seconds))
        data = w.readframes(n)
    s = struct.unpack("<%dh" % (len(data) // 2), data)
    if len(s) < 1000:
        return 0.0
    best_lag = best = 0
    lo, hi = int(sr / 500), int(sr / 80)
    for lag in range(lo, hi, max(1, (hi - lo) // 200)):
        acc = 0
        for i in range(0, len(s) - lag, 3):
            acc += s[i] * s[i + lag]
        if acc > best:
            best, best_lag = acc, lag
    return sr / best_lag if best_lag else 0.0


def zc_all(raw):
    with wave.open(io.BytesIO(raw), "rb") as w:
        data = w.readframes(w.getnframes())
    s = struct.unpack("<%dh" % (len(data) // 2), data)
    return sum(1 for i in range(1, len(s)) if (s[i - 1] < 0) != (s[i] < 0)) / len(s)


TEXT = "哼，本小姐出手还不是手到擒来！"
OK = []


async def run_group(name, seed, temp, n=3):
    FakeSelf.mimo_tts_seed = seed
    FakeSelf.mimo_tts_temperature = temp
    f0s, zcs = [], []
    for _ in range(n):
        b64 = await ttsmod.tts_http_stream(FakeSelf(), TEXT, "")
        if not b64:
            print("  %s 合成失败" % name)
            return None
        raw = base64.b64decode(b64)
        f0s.append(f0_head(raw))
        zcs.append(zc_all(raw))
        await asyncio.sleep(2)
    df = (max(f0s) - min(f0s)) / max(1.0, max(f0s)) * 100
    dz = (max(zcs) - min(zcs)) / max(1e-6, max(zcs)) * 100
    print("  %-22s 基频 %s Hz" % (name, " / ".join("%.0f" % x for x in f0s)))
    print("  %-22s 基频波动 %.1f%%   过零率波动 %.1f%%" % ("", df, dz))
    return df, dz


async def main():
    print("=" * 72)
    print("A. 修复后（seed=42 + temperature=0 + 音色标签固定）")
    print("=" * 72)
    a = await run_group("新默认", 42, 0, 3)
    print()
    print("=" * 72)
    print("B. 修复前（无 seed、temperature 默认、标签随机）")
    print("=" * 72)
    b = await run_group("旧行为", 0, 1.0, 3)
    print()
    print("=" * 72)
    if a and b:
        print("基频波动: %.1f%% → %.1f%%（改善 %.0f%%）" % (b[0], a[0], (1 - a[0] / b[0]) * 100))
        print("过零率波动: %.1f%% → %.1f%%" % (b[1], a[1]))
        OK.append(a[0] <= b[0])
        OK.append(a[0] < 6.0)
    print("=" * 72)
    print("结果: %d/%d 通过" % (sum(OK), len(OK)))
    print("=" * 72)
    return 0 if all(OK) else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))
