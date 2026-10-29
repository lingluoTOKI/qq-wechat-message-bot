import asyncio
import importlib
import inspect
import json
import os
import sys

sys.path.insert(0, "/AstrBot/data/plugins")
import astrbot_plugin_clonetts.main as m

OK = []


def ck(name, cond, extra=""):
    OK.append(bool(cond))
    print("%s  %-30s %s" % ("PASS" if cond else "FAIL", name, extra))


ck("模块导入", True)
ck("命令存在", hasattr(m.CloneTTSPlugin, "voice_mode_cmd"))
ck("是异步生成器", inspect.isasyncgenfunction(m.CloneTTSPlugin.voice_mode_cmd))
ck("状态文件路径正确", m._VM_PATH.endswith(
    "/data/plugin_data/astrbot_plugin_clonetts/voice_mode.json"), m._VM_PATH)

# 读写往返
UMO = "test:umo:verify"
try:
    os.remove(m._VM_PATH)
except FileNotFoundError:
    pass
m._vm_cache = None
ck("初始为关", m._vm_get(UMO) is False)
m._vm_set(UMO, True)
ck("写入后为开", m._vm_get(UMO) is True)
ck("文件已生成", os.path.exists(m._VM_PATH))
with open(m._VM_PATH, encoding="utf-8") as f:
    ck("落盘内容正确", json.load(f).get(UMO) is True)

# 模拟重启：清内存缓存后重新读
m._vm_cache = None
ck("重启后仍为开（持久化）", m._vm_get(UMO) is True)

# 关掉
m._vm_set(UMO, False)
m._vm_cache = None
ck("关闭后为关（不残留）", m._vm_get(UMO) is False)

# 会话隔离
m._vm_set("chat:A", True)
m._vm_set("chat:B", False)
ck("会话隔离", m._vm_get("chat:A") is True and m._vm_get("chat:B") is False)
m._vm_set("chat:A", False)


# 驱动命令
class FakeEvent:
    def __init__(self, text, umo="cmd:umo"):
        self.message_str = text
        self.unified_msg_origin = umo

    def plain_result(self, t):
        return ("PLAIN", t)


async def drive(text, umo="cmd:umo"):
    inst = object.__new__(m.CloneTTSPlugin)
    out = []
    async for r in m.CloneTTSPlugin.voice_mode_cmd(inst, FakeEvent(text, umo)):
        out.append(r)
    return out


loop = asyncio.new_event_loop()
try:
    o = loop.run_until_complete(drive("/语音 开"))
    ck("/语音 开 → 确认", len(o) == 1 and "语音模式" in o[0][1], o[0][1][:24])
    ck("  状态已打开", m._vm_get("cmd:umo") is True)

    o = loop.run_until_complete(drive("/语音 关"))
    ck("/语音 关 → 确认", len(o) == 1 and "文字" in o[0][1])
    ck("  状态已关闭", m._vm_get("cmd:umo") is False)

    o = loop.run_until_complete(drive("/语音"))
    ck("/语音（无参）→ 切换为开", m._vm_get("cmd:umo") is True, o[0][1][:24])

    o = loop.run_until_complete(drive("/语音"))
    ck("/语音（再切）→ 关闭", m._vm_get("cmd:umo") is False)

    o = loop.run_until_complete(drive("/语音 状态"))
    ck("/语音 状态 → 显示状态", len(o) == 1 and "现在是" in o[0][1], o[0][1][:30])
finally:
    loop.close()

# 清理测试残留
for k in ("cmd:umo", "chat:A", "chat:B"):
    m._vm_set(k, False)
try:
    if os.path.getsize(m._VM_PATH) <= 3:
        os.remove(m._VM_PATH)
except Exception:
    pass

print()
print("结果: %d/%d 通过" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
