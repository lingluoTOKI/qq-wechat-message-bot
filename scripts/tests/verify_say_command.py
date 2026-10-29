import sys
sys.path.insert(0, "/AstrBot/data/plugins")

import astrbot_plugin_clonetts.main as m

print("模块导入: OK")
print("类存在 say_cmd:", hasattr(m.CloneTTSPlugin, "say_cmd"))

import inspect
print("是异步生成器:", inspect.isasyncgenfunction(m.CloneTTSPlugin.say_cmd))

src = inspect.getsource(m.CloneTTSPlugin.say_cmd)
for k in ("chain_result", "Record.fromBase64", "_split_tts_parts", "tts_http_stream"):
    print("  用到 %-22s %s" % (k, "OK" if k in src else "!! 缺"))

# 命令是否真的注册进了 handler 列表
try:
    from astrbot.core.star.star_handler import star_handlers_registry
    names = []
    for h in star_handlers_registry:
        hd = getattr(h, "handler_name", "") or ""
        if "say_cmd" in hd or "说" in str(getattr(h, "command_name", "")):
            names.append(hd)
    print("注册的 say_cmd handler:", names or "(找不到)")
except Exception as e:
    print("查注册表失败:", type(e).__name__, e)

# 驱动一次 say_cmd（不联网：给空文本走用法提示分支）
import asyncio


class FakeEvent:
    message_str = "/说"

    def plain_result(self, t):
        return ("PLAIN", t)

    def chain_result(self, c):
        return ("CHAIN", c)


async def drive():
    inst = object.__new__(m.CloneTTSPlugin)
    inst.enable_tts = True
    inst.tts_split_sentences = True
    inst.tts_max_parts = 4
    inst.tts_part_interval = 0.0
    out = []
    async for r in m.CloneTTSPlugin.say_cmd(inst, FakeEvent()):
        out.append(r)
    return out


outs = asyncio.get_event_loop().run_until_complete(drive())
print("空文本 -> 产出 %d 条:" % len(outs), outs)
assert len(outs) == 1 and outs[0][0] == "PLAIN", "空文本应回用法提示"
assert "用法" in outs[0][1], "提示语不对"
print("空文本分支: PASS")


# 有文本但 TTS 不可用（把 url 指向黑洞）→ 应回「合成失败」而不是抛异常
class FakeEvent2(FakeEvent):
    message_str = "/说 测试一下"


orig = m.tts_http_stream


async def _boom(self, text, ctx):
    return None


m.tts_http_stream = _boom
try:
    async def drive2():
        inst = object.__new__(m.CloneTTSPlugin)
        inst.enable_tts = True
        inst.tts_split_sentences = True
        inst.tts_max_parts = 4
        inst.tts_part_interval = 0.0
        out = []
        async for r in m.CloneTTSPlugin.say_cmd(inst, FakeEvent2()):
            out.append(r)
        return out

    outs2 = asyncio.get_event_loop().run_until_complete(drive2())
    print("合成失败 -> 产出 %d 条:" % len(outs2), outs2)
    assert len(outs2) == 1 and "失败" in outs2[0][1], "应给出失败提示"
    print("失败分支: PASS")
finally:
    m.tts_http_stream = orig

print("全部通过")
