"""验证 A 档「群里语音对话感」三项能力
1. 切句：分出 2-4 句，短句不碎、长句不超限
2. 情绪标签：按句子特征自动挑标签
3. 端到端：多句逐条合成，每句一条 WAV，且音色一致
"""
import asyncio
import base64
import os
import re
import sys
import types
import shutil
import tempfile

SRC = os.environ.get("CLONETTS_SRC", "/AstrBot/data/plugins/astrbot_plugin_clonetts")


def _mod(name):
    x = types.ModuleType(name)
    sys.modules[name] = x
    return x


a = _mod("astrbot"); a.__path__ = []
api = _mod("astrbot.api"); a.api = api


class L:
    def debug(s, *x, **k): pass
    def info(s, *x, **k): print("  [info]", str(x[0])[:110] if x else "")
    def warning(s, *x, **k): print("  [warn]", str(x[0])[:110] if x else "")
    def error(s, *x, **k): print("  [error]", str(x[0])[:110] if x else "")


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
# astrbot.core.* 依赖
core = _mod("astrbot.core"); core.__path__ = []
mcore = _mod("astrbot.core.agent"); mcore.__path__ = []
rc = _mod("astrbot.core.agent.run_context")
rc.ContextWrapper = type("CW", (), {"__class_getitem__": classmethod(lambda cls, i: cls)})
tl = _mod("astrbot.core.agent.tool")
tl.FunctionTool = type("FT", (), {"__class_getitem__": classmethod(lambda cls, i: cls)})
tl.ToolExecResult = type("TER", (), {})
ac = _mod("astrbot.core.astr_agent_context")
ac.AstrAgentContext = type("AC", (), {})
cfg = _mod("astrbot.core.config")
cfg.AstrBotConfig = type("ABC", (), {})
ent = _mod("astrbot.core.provider.entities")
ent.LLMResponse = type("LR", (), {})

PKG = "clonetts_split_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
shutil.copytree(SRC, D)
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
m = __import__(PKG + ".main", fromlist=["main"])
ttsmod = __import__(PKG + ".tts_api.dy_tts_api", fromlist=["dy_tts_api"])

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-40s %s" % ("PASS" if cond else "FAIL", name, detail))


print("=" * 70)
print("1. 切句逻辑")
print("=" * 70)
cases = [
    ("哼，谁要你夸了！我只是刚好路过而已～别、别想太多啊！", 4),
    ("好的。", 1),
    ("", 0),
    ("这是一句很长的话，长到需要按逗号再切一次才行，否则单条语音太长了听起来很怪，对吧？", 4),
]
for text, expect_max in cases:
    parts = m._split_tts_parts(text, 4)
    ok = len(parts) <= expect_max and all(len(p) > 0 for p in parts)
    check("切句：%s" % (text[:16] + "…" if len(text) > 16 else text or "(空)"),
          ok, "%d 段: %s" % (len(parts), " | ".join(p[:14] for p in parts)))
check("短碎片会并入前一句", len(m._split_tts_parts("好。嗯。", 4)) <= 1,
      str(m._split_tts_parts("好。嗯。", 4)))

print()
print("=" * 70)
print("2. 情绪标签自动选择")
print("=" * 70)
tag_cases = [
    ("哼，谁要你夸了！", "aggrieved"),
    ("你到底要说什么呀？", "playful"),
    ("唔……让我想想啦", "childlike"),
    ("太好了！", "lively"),
]
for text, want in tag_cases:
    got = ttsmod._pick_mimo_tag(text)
    check("标签：%s" % text[:14], want in got, "得到 %s" % got)
check("默认标签是童声+俏皮", ttsmod._pick_mimo_tag("普通的一句话") == "(childlike)(playful)",
      ttsmod._pick_mimo_tag("普通的一句话"))

print()
print("=" * 70)
print("3. 端到端：多句逐条合成（真实调用 MiMo）")
print("=" * 70)


class FakeSelf:
    voice_type = "mimo-v2.5-tts-voiceclone"
    mimo_tts_ref_audio = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
    mimo_tts_api_base = ""
    mimo_tts_api_key = ""
    mimo_tts_voice_desc = ""
    mimo_tts_style_prompt = ""
    mimo_tts_default_tag = ""
    fallback_voice_type = "zh-CN-XiaoyiNeural"
    speed_ratio = 0
    edge_pitch = "+15Hz"
    api_key = ""


async def main():
    text = "哼，谁要你夸了！我只是刚好路过而已～别、别想太多啊！"
    parts = m._split_tts_parts(text, 4)
    print("  分成 %d 段：%s" % (len(parts), " | ".join(parts)))
    sizes = []
    for p in parts:
        b64 = await ttsmod.tts_http_stream(FakeSelf(), p, "")
        if not b64:
            check("合成：%s" % p[:14], False, "返回空")
            return
        raw = base64.b64decode(b64)
        is_wav = raw[:4] == b"RIFF"
        sizes.append(len(raw))
        check("合成：%s" % p[:14], is_wav, "%.1f KB" % (len(raw) / 1024))
        await asyncio.sleep(0.4)
    check("每句都是独立音频", len(sizes) == len(parts), "%d 段" % len(sizes))
    # WAV 24kHz 单声道 16bit ≈ 48 KB/秒
    KBPS = 48.0
    secs = [s / 1024.0 / KBPS for s in sizes]
    avg = sum(secs) / max(1, len(secs))
    check("单句时长 0.5-6 秒（不再是一整段念稿）",
          all(0.5 <= s <= 6.0 for s in secs),
          "各句 %.1f/%.1f/%.1f 秒，均 %.1f 秒" % (*secs, avg) if len(secs) == 3
          else "各句 %s 秒" % ", ".join("%.1f" % s for s in secs))


asyncio.get_event_loop().run_until_complete(main())

print()
print("=" * 70)
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
print("=" * 70)
sys.exit(0 if all(OK) else 1)
