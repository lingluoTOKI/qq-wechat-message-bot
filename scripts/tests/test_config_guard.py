"""测试配置守卫：模拟面板误开「显示工具调用」→ 是否自动关回
用配置文件副本测试，不碰生产配置
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = "/opt/bot-stack/astrbot/data/cmd_config.json"
TMP = os.path.join(tempfile.gettempdir(), "cmd_config_test.json")

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print("%s  %-42s %s" % ("PASS" if cond else "FAIL", name, detail))


def read_switches(path):
    with open(path, encoding="utf-8-sig") as f:
        d = json.load(f)
    ps = d.get("provider_settings", {})
    return ps.get("show_tool_call_result"), ps.get("show_tool_use_status"), d


def write_switches(path, val):
    with open(path, encoding="utf-8-sig") as f:
        d = json.load(f)
    d.setdefault("provider_settings", {})["show_tool_call_result"] = val
    d["provider_settings"]["show_tool_use_status"] = val
    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


print("=" * 68)
print("场景 1：开关为 False（正常）→ 不应改动")
print("=" * 68)
shutil.copy(SRC, TMP)
write_switches(TMP, False)
r = subprocess.run([sys.executable, "/opt/bot-stack/bin/deps_health_notify.py"],
                   env={**os.environ, "DEPS_CFG_PATH": TMP},
                   capture_output=True, text=True, timeout=300)
a, b, _ = read_switches(TMP)
check("保持 False 不变", a is False and b is False, "call_result=%s use_status=%s" % (a, b))

print()
print("=" * 68)
print("场景 2：开关被误开成 True → 应自动关回")
print("=" * 68)
write_switches(TMP, True)
r = subprocess.run([sys.executable, "/opt/bot-stack/bin/deps_health_notify.py"],
                   env={**os.environ, "DEPS_CFG_PATH": TMP},
                   capture_output=True, text=True, timeout=300)
a, b, d = read_switches(TMP)
check("自动关回 False", a is False and b is False, "call_result=%s use_status=%s" % (a, b))
check("其它配置未被破坏",
      len(d.get("provider", [])) == 4 and len(d.get("provider_sources", [])) == 5
      and len(d.get("platform", [])) == 2,
      "provider=%s sources=%s platform=%s" % (len(d.get("provider", [])),
                                             len(d.get("provider_sources", [])),
                                             len(d.get("platform", []))))
guard_log = [l for l in r.stdout.splitlines() if "配置守卫" in l]
check("日志有守卫记录", bool(guard_log), guard_log[-1][-60:] if guard_log else "无")

print()
print("=" * 68)
print("场景 3：生产配置当前状态")
print("=" * 68)
a, b, _ = read_switches(SRC)
check("生产配置已是 False", a is False and b is False, "call_result=%s use_status=%s" % (a, b))

os.remove(TMP)
print()
print("=" * 68)
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
print("=" * 68)
sys.exit(0 if all(OK) else 1)
