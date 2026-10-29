#!/usr/bin/env python3
"""修复 clonetts 两个 bug（2026-10-26 语音失败根因）。

Bug 1  MiMo 回落永远失败：
        tts_api/dy_tts_api.py 的 _load_mimo_key() 用 encoding="utf-8" 读
        cmd_config.json，而该文件被 AstrBot 面板保存后带 UTF-8 BOM，
        json.load 直接抛 JSONDecodeError → 返回空 key → "MiMo TTS：未找到 API Key"。
        改 utf-8-sig。

Bug 2  分句连发必崩：
        main.py 用 Comp.MessageChain(...)，但 astrbot.api.message_components
        里没有 MessageChain（它在 astrbot.api.event 里）。
        → module 'astrbot.api.message_components' has no attribute 'MessageChain'

两个都是"改完没实测到"的低级错误：第一个是 BOM，第二个是导入路径。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts")
DTT = BASE / "tts_api" / "dy_tts_api.py"
MAIN = BASE / "main.py"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")

for p in (DTT, MAIN):
    shutil.copy(p, "%s.bak-voicefix-%s" % (p, STAMP))
print("已备份:", STAMP)


def patch(path, pairs, label):
    src = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in src:
            print("  !! %s 未匹配: %r" % (label, old[:60]))
            return False
        src = src.replace(old, new, 1)
    path.write_text(src, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    print("  %s 已修，语法通过" % label)
    return True


# ---- Bug 1: BOM ----
ok1 = patch(DTT, [
    ('with open(MIMO_CMD_CONFIG, encoding="utf-8") as f:',
     'with open(MIMO_CMD_CONFIG, encoding="utf-8-sig") as f:'),
], "dy_tts_api._load_mimo_key 改 utf-8-sig")

# ---- Bug 2: MessageChain 导入 ----
ok2 = patch(MAIN, [
    ("from astrbot.api.event import AstrMessageEvent, filter",
     "from astrbot.api.event import AstrMessageEvent, MessageChain, filter"),
    ("await event.send(Comp.MessageChain([Comp.Record.fromBase64(b64)]))",
     "await event.send(MessageChain([Comp.Record.fromBase64(b64)]))"),
], "main.py 改用 astrbot.api.event.MessageChain")

print()
print("=== 校验 ===")
s1 = DTT.read_text(encoding="utf-8")
s2 = MAIN.read_text(encoding="utf-8")
print("  utf-8-sig 出现:", s1.count('encoding="utf-8-sig"'))
print("  Comp.MessageChain 残留:", s2.count("Comp.MessageChain"))
print("  MessageChain 导入:", "AstrMessageEvent, MessageChain, filter" in s2)
print()
print("结果:", "全部修复" if (ok1 and ok2) else "有未匹配项，请检查")
