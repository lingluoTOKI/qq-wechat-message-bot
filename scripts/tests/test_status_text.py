"""验证 /服务器状态 新增的服务端细节输出（真实打 /stability 接口）"""
import os
import sys
import types
import shutil
import tempfile
import asyncio

SRC = os.environ.get("WUSHU_SRC", "/AstrBot/data/plugins/astrbot_plugin_wushu_ops")


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
st.StarTools = type("T", (), {"get_data_dir": staticmethod(lambda n: "/AstrBot/data/plugin_data/wushu_ops")})
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

PKG = "wushu_status_test"
D = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(D):
    shutil.rmtree(D)
os.makedirs(D)
for f in os.listdir(SRC):
    if f.endswith(".py"):
        shutil.copy(os.path.join(SRC, f), os.path.join(D, f))
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
m = __import__(PKG + ".main", fromlist=["main"])

text = asyncio.get_event_loop().run_until_complete(m._build_status_text())
print("=" * 58)
print(text)
print("=" * 58)

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-34s %s" % ("PASS" if cond else "FAIL", name, detail))


check("输出含标题", "服务器状态" in text)
check("含主机内存", "内存剩余" in text)
check("含服务端进程细节", "服务端：" in text, "新增字段")
check("含在线玩家数", "在线" in text)
check("含准入延迟", "延迟" in text and "ms" in text)
check("无异常占位", "None" not in text and "查询出错" not in text)
check("无未替换占位", "?" not in text.split("版本")[-1][:40] if "版本" in text else True)

print()
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
