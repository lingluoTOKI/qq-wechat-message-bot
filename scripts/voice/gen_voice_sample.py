"""生成修复后的「分句连发」真实样音，供人工听感验收
走插件实际代码路径：_split_tts_parts + tts_http_stream
"""
import asyncio
import base64
import os
import sys
import types
import shutil
import tempfile

SRC = os.environ.get("CLONETTS_SRC", "/AstrBot/data/plugins/astrbot_plugin_clonetts")
OUT = "/AstrBot/data/plugin_data/mimo_tts_samples"


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

PKG = "ct_sample"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
shutil.copytree(SRC, D)
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
main_mod = __import__(PKG + ".main", fromlist=["main"])
ttsmod = __import__(PKG + ".tts_api.dy_tts_api", fromlist=["dy_tts_api"])

REPLY = ("哼，本小姐今天心情不错，勉为其难夸你一句吧！"
         "你呀，就乖乖等着听好消息好了～"
         "……才、才不是想让你夸我呢！")


class FakeSelf:
    voice_type = "mimo-v2.5-tts-voiceclone"
    mimo_tts_ref_audio = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
    mimo_tts_api_base = ""
    mimo_tts_api_key = ""
    mimo_tts_voice_desc = ""
    mimo_tts_style_prompt = ""
    mimo_tts_default_tag = ""
    mimo_tts_voice_tag = "(childlike)"
    mimo_tts_emotion_tag = ""
    mimo_tts_seed = 42
    mimo_tts_temperature = 0
    fallback_voice_type = "zh-CN-XiaoyiNeural"
    speed_ratio = 0
    edge_pitch = "+15Hz"
    api_key = ""


async def run():
    parts = main_mod._split_tts_parts(REPLY, 4)
    print("原句：%s" % REPLY)
    print("切分 %d 段：" % len(parts))
    for i, p in enumerate(parts):
        emo = ttsmod._pick_emotion_tag(p)
        voice = FakeSelf.mimo_tts_voice_tag
        print("  第%d句: %s" % (i + 1, p))
        print("         音色标签 %s（固定） + 情绪标签 %s（可变）" % (voice, emo))
        b64 = await ttsmod.tts_http_stream(FakeSelf(), p, "")
        if not b64:
            print("         合成失败")
            continue
        raw = base64.b64decode(b64)
        path = os.path.join(OUT, "sample_%d.wav" % (i + 1))
        with open(path, "wb") as f:
            f.write(raw)
        print("         → %s (%.1f KB)" % (os.path.basename(path), len(raw) / 1024))
        await asyncio.sleep(0.4)


asyncio.run(run())
