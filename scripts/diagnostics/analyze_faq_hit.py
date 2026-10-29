"""离线统计群消息的 FAQ / 知识库命中情况。

做法：从容器日志提取真实群消息，用插件自身的 match_faq / search_knowledge 跑一遍，
得出「有多少问题被本地知识覆盖、有多少落到模型兜底（即答不上来）」。
全部本地执行，不消耗任何 token。
"""
import os
import re
import subprocess
import sys
import tempfile
import types

HOURS = os.environ.get("HOURS", "6")
PLUGIN = os.environ.get("WUSHU_SRC", "/AstrBot/data/plugins/astrbot_plugin_wushu_ops")
DATA = os.environ.get("WUSHU_DATA", "/AstrBot/data/plugin_data/wushu_ops")

# ---------------- 桩件：astrbot ----------------
def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


class _Logger:
    def debug(self, *a, **k):
        pass
    info = debug
    warning = debug
    error = debug


astrbot = _mod("astrbot")
astrbot.__path__ = []
api = _mod("astrbot.api")
astrbot.api = api
api.logger = _Logger()
star = _mod("astrbot.api.star")
star.Context = type("Context", (), {})
star.Star = type("Star", (), {"__init__": lambda self, c: None})
star.register = lambda *a, **k: (lambda f: f)
star.StarTools = type("StarTools", (), {"get_data_dir": staticmethod(lambda n: DATA)})
api.star = star
ev = _mod("astrbot.api.event")
ev.filter = type("F", (), {
    "EventMessageType": type("E", (), {"ALL": 1}),
    "command": staticmethod(lambda *a, **k: (lambda f: f)),
    "event_message_type": staticmethod(lambda *a, **k: (lambda f: f)),
    "llm_tool": staticmethod(lambda *a, **k: (lambda f: f)),
    "on_llm_request": staticmethod(lambda *a, **k: (lambda f: f)),
})()
ev.AstrMessageEvent = type("AstrMessageEvent", (), {})
ev.MessageChain = type("MessageChain", (), {"__init__": lambda self, c=None: None})
api.event = ev
mc = _mod("astrbot.api.message_components")
mc.Plain = type("Plain", (), {"__init__": lambda self, t: None})
api.message_components = mc

# ---------------- 组装临时包 ----------------
PKG = "wushu_pkg"
PKG_DIR = os.path.join(tempfile.gettempdir(), PKG)
if os.path.exists(PKG_DIR):
    import shutil
    shutil.rmtree(PKG_DIR)
os.makedirs(PKG_DIR)
import shutil
for fn in os.listdir(PLUGIN):
    if fn.endswith(".py"):
        shutil.copy(os.path.join(PLUGIN, fn), os.path.join(PKG_DIR, fn))
open(os.path.join(PKG_DIR, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())

main_mod = __import__(PKG + ".main", fromlist=["main"])
main_mod.DATA_DIR = DATA
main_mod.KNOWLEDGE_PATH = os.path.join(DATA, "knowledge.md")

# ---------------- 读取插件配置 ----------------
import json
cfg_path = os.path.join(os.path.dirname(DATA).replace("plugin_data", "config"), "wushu_ops_config.json")
auto_faq, require_q = True, True
for cand in (cfg_path, "/AstrBot/data/config/astrbot_plugin_wushu_ops_config.json"):
    if os.path.exists(cand):
        try:
            c = json.load(open(cand, encoding="utf-8"))
            auto_faq = bool(c.get("auto_faq", True))
            require_q = bool(c.get("faq_require_question", True))
        except Exception:
            pass
        break

print("插件配置: auto_faq=%s, faq_require_question=%s" % (auto_faq, require_q))

# ---------------- 提取群消息 ----------------
LOG_FILE = os.environ.get("LOG_FILE", "")
if LOG_FILE and os.path.exists(LOG_FILE):
    # 容器内无 docker，日志由宿主机导出后传入
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        raw = f.read()
else:
    raw = subprocess.run(
        ["docker", "compose", "-f", "/opt/bot-stack/docker-compose.yml", "logs", "--since", HOURS + "h", "astrbot"],
        capture_output=True, text=True, cwd="/opt/bot-stack",
    ).stdout

ANSI = re.compile(r"\x1b\[[0-9;]*m")
raw = ANSI.sub("", raw)

BOT_NAMES = set(x for x in os.environ.get("BOT_NAMES", "爱吃饭的大肥鱼,大肥鱼").split(",") if x)

LINE = re.compile(r"core\.event_bus:117\]: \[default\] \[大肥鱼-官方\(qq_official\)\] (.+?)/([0-9A-Fa-f]+): (.*)$")
msgs = []
for line in raw.splitlines():
    m = LINE.search(line)
    if not m:
        continue
    nick, _uid, text = m.group(1), m.group(2), m.group(3)
    text = text.strip()
    if not text or text.startswith("[ComponentType.") or text.startswith("[引用消息"):
        continue
    if nick in BOT_NAMES:
        continue  # 机器人自己的回复不算「用户提问」
    msgs.append((nick, text))

print("近 %s 小时群消息: %s 条（已过滤图片/文件/引用）\n" % (HOURS, len(msgs)))

MARKERS = ["?", "？", "吗", "呢", "怎么", "什么", "为啥", "为什么", "多少", "哪", "几", "如何"]

hit, miss, not_question = [], [], []
for nick, text in msgs:
    if text.startswith(("/", "#", ".")):
        continue
    entry = main_mod.match_faq(text)
    is_q = any(mk in text for mk in MARKERS)
    if entry:
        hit.append((nick, text, entry.get("id")))
    elif not is_q:
        not_question.append((nick, text))
    else:
        miss.append((nick, text))

total = len(hit) + len(miss) + len(not_question)
print("=== 本地知识覆盖情况 ===")
print("  FAQ 命中（本地直接回，0 token）: %s 条" % len(hit))
print("  疑似提问但 FAQ 未命中        : %s 条" % len(miss))
print("  非提问（闲聊/陈述/回应）      : %s 条" % len(not_question))
if len(hit) + len(miss):
    rate = len(hit) / (len(hit) + len(miss)) * 100
    print("  提问命中率                   : %.1f%%" % rate)

print("\n=== FAQ 未命中的提问（这些要靠模型兜底）===")
for nick, text in miss[:25]:
    kn = main_mod.search_knowledge(text)
    covered = "知识库有内容" if kn else "知识库也无"
    print("  %-8s %-32s [%s, %s 字]" % (nick[:8], text[:32], covered, len(kn)))

print("\n=== FAQ 命中分布 ===")
from collections import Counter
c = Counter(e for _, _, e in hit)
for k, v in c.most_common(12):
    print("  %-12s %s 次" % (k, v))
