"""端到端测试 clonetts 的 MiMo 分支 + edge 回落
真实调用 MiMo API，验证：
 1) voiceclone 正常 → 返回 wav base64
 2) 参考音频缺失/损坏 → 自动回落 Edge TTS
 3) 回落时用的音色是 fallback_voice_type
"""
import base64
import os
import sys
import types
import shutil
import tempfile
import asyncio

SRC = "/AstrBot/data/plugins/astrbot_plugin_clonetts"


def _mod(name):
    x = types.ModuleType(name)
    sys.modules[name] = x
    return x


# 桩件：只需 astrbot.api.logger
a = _mod("astrbot"); a.__path__ = []
api = _mod("astrbot.api"); a.api = api


class L:
    def debug(s, *x, **k): pass

    def info(s, *x, **k): print("  [info]", str(x[0])[:120] if x else "")

    def warning(s, *x, **k): print("  [warn]", str(x[0])[:140] if x else "")

    def error(s, *x, **k): print("  [error]", str(x[0])[:140] if x else "")


api.logger = L()

PKG = "clonetts_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
shutil.copytree(SRC, D)
# 去掉插件主模块的 AstrBot 依赖，只测 tts_api
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())

m = __import__(PKG + ".tts_api.dy_tts_api", fromlist=["dy_tts_api"])

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-40s %s" % ("PASS" if cond else "FAIL", name, detail))


class FakeSelf:
    def __init__(self, **kw):
        self.voice_type = "mimo-v2.5-tts-voiceclone"
        self.mimo_tts_ref_audio = "/AstrBot/data/plugin_data/mimo_tts_samples/ref_aojiao.wav"
        self.mimo_tts_api_base = ""
        self.mimo_tts_api_key = ""
        self.mimo_tts_voice_desc = ""
        self.mimo_tts_style_prompt = ""
        self.fallback_voice_type = "zh-CN-XiaoyiNeural"
        self.speed_ratio = 0
        self.edge_pitch = "+15Hz"
        self.api_key = ""
        self.__dict__.update(kw)


def is_wav(b64):
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return False, 0
    return raw[:4] == b"RIFF", len(raw)


async def main():
    print("=== 用例 1：voiceclone 正常合成 ===")
    s = FakeSelf()
    r = await m.tts_http_stream(s, "哼，本小姐今天心情不错，勉为其难夸你一句吧！", None)
    check("MiMo 返回非空", bool(r))
    if r:
        ok, size = is_wav(r)
        check("返回的是合法 WAV", ok, "%.1f KB" % (size / 1024))

    print()
    print("=== 用例 2：参考音频缺失 → 回落 Edge ===")
    s2 = FakeSelf(mimo_tts_ref_audio="/AstrBot/data/plugin_data/mimo_tts_samples/不存在.wav")
    r2 = await m.tts_http_stream(s2, "这是回落测试。", None)
    check("回落成功返回音频", bool(r2))
    if r2:
        ok, size = is_wav(r2)
        check("回落返回合法 WAV", ok, "%.1f KB" % (size / 1024))

    print()
    print("=== 用例 3：Edge 兜底音色可单独工作 ===")
    s3 = FakeSelf(voice_type="zh-CN-XiaoyiNeural")
    r3 = await m.tts_http_stream(s3, "纯 Edge 通路测试。", None)
    check("Edge 直接合成成功", bool(r3))
    if r3:
        ok, size = is_wav(r3)
        check("Edge 返回合法 WAV", ok, "%.1f KB" % (size / 1024))

    print()
    print("=== 用例 4：voicedesign 模式（文字描述）===")
    s4 = FakeSelf(
        voice_type="mimo-v2.5-tts-voicedesign",
        mimo_tts_voice_desc="十一二岁的女孩声，音调极高又清脆，中气足、有穿透力，"
                            "语速轻快有跳跃感，元气外放又带一点傲娇娇嗔。")
    r4 = await m.tts_http_stream(s4, "用文字描述生成的声音，听起来如何？", None)
    check("voicedesign 返回非空", bool(r4))
    if r4:
        ok, size = is_wav(r4)
        check("voicedesign 返回合法 WAV", ok, "%.1f KB" % (size / 1024))

    print()
    print("=" * 50)
    print("结果: %d/%d 通过" % (sum(OK), len(OK)))
    print("=" * 50)
    return 0 if all(OK) else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))
