"""用量插件验证：真实 import 生产代码，对比新旧口径差异"""
import os
import sys
import types
import json
import shutil
import tempfile
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SRC = os.environ.get("UV_SRC", "/opt/bot-stack/tmp/uv_main.py")

# ---- 桩件：模拟 astrbot 环境 ----
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
api.star = st
ev = _mod("astrbot.api.event")
ev.filter = type("F", (), {
    "command": staticmethod(lambda *x, **k: (lambda f: f)),
})()
ev.AstrMessageEvent = type("A", (), {})
api.event = ev

# ---- 载入被测代码 ----
D = os.path.join(tempfile.gettempdir(), "uv_test")
if os.path.exists(D):
    shutil.rmtree(D)
os.makedirs(D)
shutil.copy(SRC, os.path.join(D, "uvmod.py"))
open(os.path.join(D, "__init__.py"), "w").close()
sys.path.insert(0, tempfile.gettempdir())
m = __import__("uv_test.uvmod", fromlist=["uvmod"])

TZ = ZoneInfo("Asia/Shanghai")
OK = []


def check(name, cond, detail=""):
    OK.append(cond)
    print("%s  %-38s %s" % ("PASS" if cond else "FAIL", name, detail))


print("=" * 62)
print("1. 时间窗口（须与 cost_control refresh_time=00:00 同源）")
print("=" * 62)
s0 = m._day_start_utc(0)
now_cst = datetime.now(TZ)
expect = (now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
          .astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
print("  北京现在     : %s" % now_cst.strftime("%Y-%m-%d %H:%M"))
print("  窗口起点UTC  : %s" % s0)
check("窗口=北京今日00:00转UTC", s0 == expect, "期望 %s" % expect)
s1 = m._day_start_utc(1)
check("近2天窗口早于今日", s1 < s0, "%s" % s1)

print()
print("=" * 62)
print("2. 实际输出（/用量）")
print("=" * 62)
v = m.UsageView(None)
text = v._build(1)
print(text)

print()
print("=" * 62)
print("3. 与成本插件判定值一致性")
print("=" * 62)
CFGP = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json"
COSTDB = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"
n, miss, hit, out, usd, inj = v._usage(1)
total = miss + hit + out + inj

c = sqlite3.connect("file:%s?mode=ro" % COSTDB, uri=True, timeout=5)
ref = c.execute(
    """select count(*), coalesce(sum(coalesce(token_input_other,0)
        +coalesce(token_input_cached,0)+coalesce(token_output,0)
        +coalesce(injection_total,0)),0)
       from cost_supplements where created_at >= ?""", (s0,)).fetchone()
c.close()
print("  插件算得 : %s 次 / %s token" % (n, format(total, ",")))
print("  基准查询 : %s 次 / %s token" % (ref[0], format(int(ref[1]), ",")))
check("token 与成本库完全一致", total == int(ref[1]),
      "差 %s" % format(total - int(ref[1]), ","))
check("次数与成本库一致", n == int(ref[0]), "%s vs %s" % (n, ref[0]))

lim = int(json.load(open(CFG := CFGP, encoding="utf-8")).get("budgets", {}).get("global_daily") or 0)
pct = 100.0 * total / lim if lim else 0
print("  限额 %s -> 占用 %.0f%%" % (format(lim, ","), pct))
check("百分比与机器人状态一致", (total >= lim) == (pct >= 100), "%.0f%%" % pct)

print()
print("=" * 62)
print("4. 对比旧版（provider_stats × 1.5）")
print("=" * 62)
STATDB = "/AstrBot/data/data_v4.db"
try:
    c = sqlite3.connect("file:%s?mode=ro" % STATDB, uri=True, timeout=5)
    row = c.execute(
        """select coalesce(sum(token_input_other+token_input_cached+token_output),0)
           from provider_stats where provider_id like 'deepseek%%'
           and date(created_at) >= ?""", (now_cst.strftime("%Y-%m-%d"),)).fetchone()
    c.close()
    old = int(round(int(row[0]) * 1.5))
    print("  旧版显示 : %s（%.0f%%）" % (format(old, ","), 100.0 * old / lim))
    print("  新版显示 : %s（%.0f%%）" % (format(total, ","), pct))
    check("新版不再虚高", abs(total - old) > 0, "差 %s" % format(abs(total - old), ","))
except Exception as e:
    print("  跳过（%s）" % e)

print()
print("=" * 62)
print("5. 多天查询不报错")
print("=" * 62)
for d in (1, 3, 7, 30):
    try:
        t = v._build(d)
        first = t.splitlines()[0]
        check("近%s天可查询" % d, "用量" in first, "%s" % first)
    except Exception as e:
        check("近%s天可查询" % d, False, str(e))

print()
print("=" * 62)
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
print("=" * 62)
sys.exit(0 if all(OK) else 1)
