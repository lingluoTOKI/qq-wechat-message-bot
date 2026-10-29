"""验证 wushu_ops 的"莫名触发"四项修复
1. match_faq 强弱关键词分级：闲聊不再误命中
2. FAQ 命中后不再原文直出（改为注入上下文）
3. inject_faq_context 把权威答案写进 system_prompt
4. 问句检测收紧（去掉 吗/呢/哪/几 这类虚词）
"""
import os
import sys
import types
import json
import shutil
import tempfile
import asyncio

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

PKG = "wushu_fix_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
os.makedirs(D)
for f in os.listdir(SRC):
    # faq.json 必须一起复制，否则 _load_faq 读不到真实词库会退回默认 FAQ
    if f.endswith(".py") or f == "faq.json":
        shutil.copy(os.path.join(SRC, f), os.path.join(D, f))
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
m = __import__(PKG + ".main", fromlist=["main"])

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-44s %s" % ("PASS" if cond else "FAIL", name, detail))


print("=" * 72)
print("1. match_faq：闲聊不再误命中")
print("=" * 72)
NO_HIT = [
    ("这游戏是官方的吗", "free（仅『官方』1个弱词）"),
    ("完全免费的吧", "free（仅『免费』1个弱词）"),
    ("这模式挺好玩的", "modes（仅『模式』1个弱词）"),
    ("我卡了", "lag（仅『卡了』1个弱词）"),
    ("这游戏是同人的", "free（仅『同人』1个弱词）"),
]
for text, why in NO_HIT:
    r = m.match_faq(text)
    check("闲聊不命中：%s" % text, r is None, why if r is None else "误命中 id=%s" % r.get("id"))

print()
print("=" * 72)
print("2. match_faq：真实提问仍能命中")
print("=" * 72)
MUST_HIT = [
    ("服务器地址是什么", "entry（强词『服务器地址』）"),
    ("游戏地址在哪", "entry（强词『游戏地址』）"),
    ("完全免费吗，要钱吗", "free（『免费』+『要钱』2个弱词）"),
    ("素材包怎么导入", "assets（『素材』+『导入』2个弱词）"),
    ("导入卡住了怎么办", "assets-stuck（强词『导入卡住』）"),
    ("掉线了怎么重连", "reconnect（强词『重连』）"),
]
for text, why in MUST_HIT:
    r = m.match_faq(text)
    check("提问能命中：%s" % text, r is not None, why if r else "未命中")

print()
print("=" * 72)
print("3. 问句检测：虚词不算提问特征")
print("=" * 72)
check("markers 不含裸『吗/呢/哪/几』",
      not any(x in m._QUESTION_MARKERS for x in ("吗", "呢", "哪", "几")),
      "markers=%d 个" % len(m._QUESTION_MARKERS))
check("markers 含真疑问词", all(x in m._QUESTION_MARKERS for x in ("怎么", "为什么", "如何", "多少")))

print()
print("=" * 72)
print("4. inject_faq_context：权威答案写入 system_prompt")
print("=" * 72)


class FakeEvent:
    def __init__(self, extras=None):
        self._extras = extras or {}

    def get_extra(self, k, default=None):
        return self._extras.get(k, default)

    def set_extra(self, k, v):
        self._extras[k] = v


class FakeReq:
    def __init__(self, sp="原始人格设定"):
        self.system_prompt = sp


async def run_inject():
    inst = m.WushuOpsPlugin.__new__(m.WushuOpsPlugin)
    # 无答案时应原样不动
    r1 = FakeReq("人格")
    await inst.inject_faq_context(FakeEvent(), r1)
    check("无答案时不改动 system_prompt", r1.system_prompt == "人格", r1.system_prompt[:40])

    # 有答案时应追加
    r2 = FakeReq("人格")
    await inst.inject_faq_context(FakeEvent({"wushu_faq_answer": "本服完全免费，是同人公益项目。"}), r2)
    check("有答案时追加到 system_prompt", "本服完全免费" in r2.system_prompt)
    check("保留原人格设定", r2.system_prompt.startswith("人格"))
    check("含『无关则忽略』免责说明", "无关" in r2.system_prompt)
    print()
    print("  注入后的 system_prompt 尾部：")
    print("  " + r2.system_prompt[-120:].replace("\n", "\n  "))


asyncio.get_event_loop().run_until_complete(run_inject())

print()
print("=" * 72)
print("5. 代码静态检查：不再有 FAQ 原文直出路径")
print("=" * 72)
src = open(os.path.join(SRC, "main.py"), encoding="utf-8").read()
check("auto_faq 内不再直接 yield FAQ 原文（仅 direct 模式保留）",
      src.count('yield event.plain_result(entry["answer"])') == 1,
      "出现 %d 次（1=仅 direct 模式）" % src.count('yield event.plain_result(entry["answer"])'))
check("存在 inject 模式默认配置", '"faq_answer_mode", "inject"' in src)
check("FAQ 冷却默认 600 秒", '"faq_cooldown_seconds", 600' in src)

print()
print("=" * 72)
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
print("=" * 72)
sys.exit(0 if all(OK) else 1)
