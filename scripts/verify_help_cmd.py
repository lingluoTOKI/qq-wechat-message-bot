import asyncio, inspect, sys
sys.path.insert(0, "/AstrBot/data/plugins")
import astrbot_plugin_dafeiyu_help.main as m
from astrbot.core.star.star_handler import star_handlers_registry

OK = []
def ck(n, c, e=""):
    OK.append(bool(c)); print("%s  %-28s %s" % ("PASS" if c else "FAIL", n, e))

ck("模块导入", True)
ck("命令存在", hasattr(m.DafeiyuHelpPlugin, "help_cmd"))
ck("异步生成器", inspect.isasyncgenfunction(m.DafeiyuHelpPlugin.help_cmd))

hs = [h for h in star_handlers_registry
      if "help_cmd" in (getattr(h, "handler_name", "") or "")]
ck("已注册 handler", len(hs) == 1, "共 %d 个" % len(hs))
if hs:
    cmds = getattr(hs[0], "command_name", None) or getattr(hs[0], "extras_configs", {})
    print("     命令/别名:", cmds)

# 文案检查
t = m.HELP_TEXT
ck("非空", bool(t.strip()), "%d 字符" % len(t))
for kw in ("/说", "/语音", "/服务器状态", "/报bug", "/搜索歌曲", "/用量", "/运营帮助"):
    ck("含命令 %s" % kw, kw in t)
ck("无 markdown 粗体", "**" not in t)

class Ev:
    def plain_result(self, x): return ("PLAIN", x)

async def drive():
    inst = object.__new__(m.DafeiyuHelpPlugin)
    out = []
    async for r in m.DafeiyuHelpPlugin.help_cmd(inst, Ev()):
        out.append(r)
    return out

loop = asyncio.new_event_loop()
outs = loop.run_until_complete(drive()); loop.close()
ck("产出 1 条", len(outs) == 1 and outs[0][0] == "PLAIN")

print("\n----- 实际输出 -----")
print(outs[0][1])
print("----- 共 %d 行 / %d 字符 -----" % (outs[0][1].count("\n") + 1, len(outs[0][1])))
print("\n结果: %d/%d" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
