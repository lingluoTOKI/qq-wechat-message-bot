"""离线冒烟测试：用桩件模拟 AstrBot 环境，真实 import wushu_ops 包并跑关键路径。

目的：验证本轮四项改动（读盘缓存 / sqlite 并发 / 锁拆分 / print→logger）
在运行期不会引入 NameError、线程异常或逻辑回退。
"""
import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

PLUGIN_SRC = os.environ.get("WUSHU_SRC", "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops")
PKG_NAME = "wushu_ops_pkg"
PKG_DIR = os.path.join("/tmp", PKG_NAME)

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s %s" % (name, detail))
    else:
        fail += 1
        print("  FAIL  %s %s" % (name, detail))


# ---------------- 桩件：astrbot ----------------
def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


class _Logger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        print("   [logger.warning]", a[:1])

    def error(self, *a, **k):
        print("   [logger.error]", a[:1])


astrbot = _mod("astrbot")
astrbot.__path__ = []
api = _mod("astrbot.api")
astrbot.api = api
api.logger = _Logger()

star = _mod("astrbot.api.star")


class Context:
    pass


class Star:
    def __init__(self, context):
        self.context = context


def register(*a, **k):
    def deco(cls):
        return cls
    return deco


class StarTools:
    DATA = tempfile.mkdtemp(prefix="wushu-data-")

    @staticmethod
    def get_data_dir(name):
        return StarTools.DATA


star.Context = Context
star.Star = Star
star.register = register
star.StarTools = StarTools
api.star = star

ev = _mod("astrbot.api.event")


class AstrMessageEvent:
    pass


class _Filter:
    EventMessageType = type("E", (), {"ALL": 1})

    @staticmethod
    def command(*a, **k):
        return lambda f: f

    @staticmethod
    def event_message_type(*a, **k):
        return lambda f: f

    @staticmethod
    def llm_tool(*a, **k):
        return lambda f: f

    @staticmethod
    def on_llm_request(*a, **k):
        return lambda f: f


ev.filter = _Filter()
ev.AstrMessageEvent = AstrMessageEvent
ev.MessageChain = type("MessageChain", (), {"__init__": lambda self, chain=None: None})
api.event = ev

mc = _mod("astrbot.api.message_components")
mc.Plain = type("Plain", (), {"__init__": lambda self, t: None})
api.message_components = mc

core = _mod("astrbot.core")
core.__path__ = []
agent = _mod("astrbot.core.agent")
agent.__path__ = []
tool = _mod("astrbot.core.agent.tool")
tool.ToolSet = type("ToolSet", (), {"__init__": lambda self, tools=None: None, "tools": []})
agent.tool = tool
core.agent = agent

# ---------------- 组装临时包 ----------------
print("== 0. 组装临时包 ==")
if os.path.exists(PKG_DIR):
    shutil.rmtree(PKG_DIR)
os.makedirs(PKG_DIR)
for fn in os.listdir(PLUGIN_SRC):
    if fn.endswith(".py"):
        shutil.copy(os.path.join(PLUGIN_SRC, fn), os.path.join(PKG_DIR, fn))
open(os.path.join(PKG_DIR, "__init__.py"), "w").close()
sys.path.insert(0, "/tmp")

try:
    pkg = importlib.import_module(PKG_NAME)
    main_mod = importlib.import_module(PKG_NAME + ".main")
    rt_mod = importlib.import_module(PKG_NAME + ".assistant_runtime")
    check("插件包可正常 import", True)
except Exception as e:
    check("插件包可正常 import", False, repr(e))
    print("\n结果: 0 通过 / 1 失败 —— 无法继续")
    sys.exit(1)

# ---------------- 1. 读盘缓存 ----------------
print("\n== 1. 读盘缓存 ==")
faq_path = main_mod.FAQ_PATH
know_path = main_mod.KNOWLEDGE_PATH

# 统计真实 open 次数
real_open = open
count = {"n": 0}


class CountingFile:
    def __init__(self, f):
        self.f = f

    def read(self, *a, **k):
        return self.f.read(*a, **k)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self.f.__exit__(*a)


def counting_open(path, *a, **k):
    if path in (faq_path, know_path):
        count["n"] += 1
    return real_open(path, *a, **k)


main_mod.open = counting_open  # 仅作用于本模块的 open 引用

main_mod._load_faq()   # 首次：文件若不存在则先落盘
e1 = main_mod._load_faq()  # 二次：文件已存在，建立缓存
count["n"] = 0
for _ in range(50):
    main_mod._load_faq()
    main_mod.match_faq("服务器地址是多少？")
check("FAQ 重复读取 50 次 → 0 次读盘", count["n"] == 0, "实际读盘 %s 次" % count["n"])
check("FAQ 缓存内容正确", len(e1) > 0, "条目 %s 条" % len(e1))

# 改文件后应立即失效
count["n"] = 0
os.utime(faq_path, (time.time() + 10, time.time() + 10))
main_mod._load_faq()
check("FAQ 文件改动后缓存失效重读", count["n"] == 1, "实际读盘 %s 次" % count["n"])

# 知识库：一次查询只读一次
kpath = main_mod.KNOWLEDGE_PATH
with real_open(kpath, "w", encoding="utf-8") as f:
    f.write("## 服务器\n主服地址 game.lingluotoki.dpdns.org\n\n## 素材\n素材包 1.6GB\n")
count["n"] = 0
out = main_mod.search_knowledge("素材包怎么导入")
check("知识库单次查询只读 1 次盘", count["n"] == 1, "实际读盘 %s 次" % count["n"])
check("知识库检索有结果", "素材" in out, "返回 %s 字" % len(out))
full = main_mod._load_knowledge()
check("小知识库全量返回（不再只给 2 章节）", len(out) == len(full), "返回 %s / 全文 %s 字" % (len(out), len(full)))
count["n"] = 0
for _ in range(20):
    main_mod.search_knowledge("素材包怎么导入")
check("知识库重复查询 20 次 → 0 次读盘", count["n"] == 0, "实际读盘 %s 次" % count["n"])

# ---------------- 2. sqlite 并发 ----------------
print("\n== 2. sqlite 并发保护 ==")
dbfile = os.path.join(tempfile.mkdtemp(), "t.sqlite")
rt = rt_mod.AssistantRuntime.__new__(rt_mod.AssistantRuntime)
import sqlite3
rt._db_lock = threading.RLock()
rt.db = sqlite3.connect(dbfile, check_same_thread=False, timeout=10)
rt.db.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT, ts REAL)")
rt.db.commit()
errors = []


def worker(n):
    try:
        for i in range(120):
            rt.put("k-%s-%s" % (n, i % 20), {"v": i})
            rt.get("k-%s-%s" % (n, i % 20))
            rt.execute("SELECT k FROM state LIMIT 5")
    except Exception as e:
        errors.append(repr(e))


ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
t0 = time.time()
for t in ts:
    t.start()
for t in ts:
    t.join()
check("8 线程 ×120 次读写无异常", not errors, errors[0] if errors else "耗时 %.2fs" % (time.time() - t0))
check("execute() 返回行列表而非 cursor", isinstance(rt.execute("SELECT k FROM state LIMIT 1"), list))
rt.close()

# ---------------- 3. 锁拆分 ----------------
print("\n== 3. 锁拆分（状态查询不阻塞报 bug）==")
plugin = main_mod.WushuOpsPlugin.__new__(main_mod.WushuOpsPlugin)
plugin._bug_lock = asyncio.Lock()
plugin._status_lock = asyncio.Lock()
plugin._lock = plugin._bug_lock
plugin._status_cache = (0, "")
plugin._status_ttl = 30
plugin._status_inflight = None
check("bug 锁与状态锁是两把不同的锁", plugin._bug_lock is not plugin._status_lock)

calls = {"n": 0}


async def fake_build():
    calls["n"] += 1
    await asyncio.sleep(0.3)  # 模拟 4 次 HTTP
    return "STATUS-BODY"


main_mod._build_status_text = fake_build


async def scenario():
    # 3 个人同时查状态 + 1 个人同时报 bug
    r1 = await asyncio.gather(plugin._status_text(), plugin._status_text(), plugin._status_text())
    bug_start = None

    async def bug():
        nonlocal bug_start
        bug_start = time.time()
        async with plugin._bug_lock:
            await asyncio.sleep(0.05)
        return "bug-done"

    status_task = asyncio.ensure_future(plugin._status_text())
    await asyncio.sleep(0.05)  # 让状态查询先占住
    t_bug = time.time()
    await bug()  # 报 bug 不应被状态查询阻塞
    blocked = time.time() - t_bug
    await status_task
    return r1, blocked


r1, blocked = asyncio.get_event_loop().run_until_complete(scenario())
check("3 个并发查询合并为 1 次探测", calls["n"] == 1, "实际探测 %s 次" % calls["n"])
check("并发查询返回一致结果", r1 == ["STATUS-BODY"] * 3)
check("报 bug 未被状态查询阻塞", blocked < 0.2, "耗时 %.3fs（状态查询需 0.3s）" % blocked)

# ---------------- 4. print 清理 ----------------
print("\n== 4. print 清理 ==")
src_all = ""
for fn in os.listdir(PLUGIN_SRC):
    if fn.endswith(".py"):
        src_all += real_open(os.path.join(PLUGIN_SRC, fn), encoding="utf-8").read()
check("插件源码中已无 print( 调试输出", "print(" not in src_all, "剩余 %s 处" % src_all.count("print("))
check("rank-debug 标记已清除", "rank-debug" not in src_all)

print("\n结果: %s 通过 / %s 失败" % (ok, fail))
sys.exit(1 if fail else 0)
