"""分析 wushu_ops 的触发情况：群消息 -> bot 回复 配对
用法: docker exec -e LOG=/tmp/x.log astrbot python /tmp/an.py
"""
import os
import re
from collections import Counter

LOG = os.environ.get("LOG", "/tmp/x.log")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
BOT_NAMES = ("爱吃饭的大肥鱼", "大肥鱼")

raw = ANSI.sub("", open(LOG, encoding="utf-8", errors="ignore").read())
lines = raw.splitlines()

IN = re.compile(r"core\.event_bus:123\]: \[default\] \[大肥鱼-官方\(qq_official\)\] (.+?)/([0-9A-Fa-f]+): (.*)$")
SEND = re.compile(r"respond\.stage:237\]: Prepare to send - (.+)$")
# 插件自己的调试输出
PLUG = re.compile(r"\[astrbot_plugin_wushu_ops\]|\[wushu_ops\]")

events = []
for ln in lines:
    m = TS.search(ln)
    if not m:
        continue
    t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    events.append((t, ln))

pend = []
pairs = []
for t, ln in events:
    mi = IN.search(ln)
    if mi:
        nick, uid, text = mi.group(1), mi.group(2), mi.group(3).strip()
        if not text or text.startswith("[At:") or text.startswith("[ComponentType"):
            continue
        if any(n in nick for n in BOT_NAMES):
            continue
        pend.append((t, nick, text))
        continue
    ms = SEND.search(ln)
    if ms:
        body = ms.group(1)
        # 群内 bot 发言："/群ID: 内容"
        gm = re.match(r"^/([0-9A-Fa-f]+):\s*(.*)$", body, re.S)
        if gm:
            content = gm.group(2).strip()
            if content and not content.startswith("..."):
                pairs.append((pend[0] if pend else None, content))
                continue
        pairs.append((pend[0] if pend else None, body.strip()[:200]))

print("=" * 70)
print("bot 在群里/私聊的发言（%d 条）" % len(pairs))
print("=" * 70)
for src, out in pairs[-25:]:
    if src:
        print("触发者: %s" % src[1])
        print("  群消息: %s" % src[2][:150].replace("\n", " "))
    print("  → bot: %s" % out[:220].replace("\n", " | "))
    print("-" * 70)

print()
print("=" * 70)
print("bot 发言首行分布（判断触发类型）")
print("=" * 70)
c = Counter()
for src, out in pairs:
    head = out.splitlines()[0][:40] if out else ""
    c[head] += 1
for k, v in c.most_common(15):
    print("  %3d 次  %s" % (v, k))
