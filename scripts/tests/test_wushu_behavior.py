"""行为级验证：模拟真实群消息走完 auto_faq，确认新逻辑（不直出、改为注入）"""
import os
import sys
import types
import shutil
import tempfile
import asyncio
import json

SRC = os.environ.get("WUSHU_SRC", "/AstrBot/data/plugins/astrbot_plugin_wushu_ops")
DATA = os.environ.get("WUSHU_DATA", "/AstrBot/data/plugin_data/wushu_ops")


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
st.StarTools = type("T", (), {"get_data_dir": staticmethod(lambda n: DATA)})
api.star = st
ev = _mod("astrbot.api.event")
ev.filter = type("F", (), {
    "EventMessageType": type("E", (), {"ALL": 1}),
    "command": staticmethod(lambda *x, **k: (lambda f: f)),
    "event_message_type": staticmethod(lambda *x, **k: (lambda f: f)),
    "llm_tool": staticmethod(lambda *x, **k: (lambda f: f)),
    "on_llm_request": staticmethod(lambda *x, **k: (lambda f: f)),
})()
ev.AstrMessageEvent = type("A", (), {})
ev.MessageChain = type("M", (), {"__init__": lambda s, c=None: None})
api.event = ev
mc = _mod("astrbot.api.message_components")
mc.Plain = type("P", (), {"__init__": lambda s, t: None})
api.message_components = mc

PKG = "wushu_behavior"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
os.makedirs(D)
for f in os.listdir(SRC):
    if f.endswith(".py") or f == "faq.json":
        shutil.copy(os.path.join(SRC, f), os.path.join(D, f))
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
m = __import__(PKG + ".main", fromlist=["main"])

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-40s %s" % ("PASS" if cond else "FAIL", name, detail))


class FakeEvent:
    def __init__(self, text, umo="大肥鱼-官方:GroupMessage:TESTGRP"):
        self.message_str = text
        self.unified_msg_origin = umo
        self._extras = {}
        self._stopped = False
        self._yields = []

    def is_private_chat(self):
        return False

    def get_extra(self, k, default=None):
        return self._extras.get(k, default)

    def set_extra(self, k, v):
        self._extras[k] = v

    def stop_event(self):
        self._stopped = True

    def get_group_id(self):
        return "TESTGRP"

    def get_sender_id(self):
        return "SENDER1"

    def plain_result(self, t):
        self._yields.append(t)
        return t


async def run():
    inst = m.WushuOpsPlugin.__new__(m.WushuOpsPlugin)
    inst.config = {"auto_faq": True, "faq_require_question": True}
    inst._cooldown = {}
    inst._ensure_monitor = lambda: None
    # 屏蔽内部 assistant 依赖，只测 auto_faq 的 FAQ 分支
    class FakeAssistant:
        class utilities:
            @staticmethod
            async def command(event):
                return None

            @staticmethod
            def moderation(event):
                return None

        @staticmethod
        async def message(event, faq):
            return None
    inst.assistant = FakeAssistant()

    print("=" * 66)
    print("A. 闲聊不应触发（也不该产生任何回复）")
    print("=" * 66)
    for text in ["这游戏是官方的吗", "完全免费的吧", "我卡了"]:
        e = FakeEvent(text)
        outs = [x async for x in inst.auto_faq(e)]
        check("闲聊静默：%s" % text, not outs and not e._stopped,
              "产出 %d 条" % len(outs))

    print()
    print("=" * 66)
    print("B. 真实提问 → 注入答案，而不是原文复读")
    print("=" * 66)
    e = FakeEvent("服务器地址是什么")
    outs = [x async for x in inst.auto_faq(e)]
    ans = e.get_extra("wushu_faq_answer")
    check("没有直接产出回复", not outs, "产出 %d 条" % len(outs))
    check("没有 stop_event（保留模型回复链路）", not e._stopped)
    check("已注入权威答案", bool(ans), (ans or "")[:44])

    print()
    print("=" * 66)
    print("C. 同一 FAQ 冷却期内不重复注入")
    print("=" * 66)
    e2 = FakeEvent("服务器地址在哪")
    outs2 = [x async for x in inst.auto_faq(e2)]
    check("冷却期内不再注入", not e2.get_extra("wushu_faq_answer"),
          "注入=%s" % (str(e2.get_extra("wushu_faq_answer"))[:30] if e2.get_extra("wushu_faq_answer") else "无"))

    print()
    print("=" * 66)
    print("D. 注入内容进 system_prompt")
    print("=" * 66)
    class FakeReq:
        system_prompt = "【人格】傲娇但不作"
    r = FakeReq()
    await inst.inject_faq_context(e, r)
    check("system_prompt 已含权威答案", "服务器" in r.system_prompt or len(r.system_prompt) > 20,
          "%d 字" % len(r.system_prompt))
    check("保留人格设定", r.system_prompt.startswith("【人格】"))

    print()
    print("=" * 66)
    print("结果: %d/%d 通过" % (sum(OK), len(OK)))
    print("=" * 66)
    return 0 if all(OK) else 1


sys.exit(asyncio.get_event_loop().run_until_complete(run()))
