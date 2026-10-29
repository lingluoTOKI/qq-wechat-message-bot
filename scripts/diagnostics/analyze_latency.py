"""分析群消息从到达到回复发出的耗时分布"""
import re
import sys
from collections import defaultdict

LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/msgs.log"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")

raw = open(LOG, encoding="utf-8", errors="ignore").read()
raw = ANSI.sub("", raw)

in_msg = re.compile(r"core\.event_bus:123\]: \[default\] \[大肥鱼-官方\(qq_official\)\]")
out_msg = re.compile(r"respond\.stage:237\]: Prepare to send - .*?:\s*(.*)$")
skip = re.compile(r"Skipping LLM request")

lines = raw.splitlines()
events = []
for ln in lines:
    m = TS.search(ln)
    if not m:
        continue
    t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    events.append((t, ln))

# 按会话分组：收到消息 -> 发出回复
pend = {}
deltas = []
skipped = 0
for t, ln in events:
    if in_msg.search(ln):
        # 取最后一段作为群号
        g = ln.split("]: [default]")[-1]
        m = re.search(r"\[大肥鱼-官方\(qq_official\)\] ([0-9A-Fa-f]+):", ln)
        key = m.group(1) if m else "?"
        pend.setdefault(key, []).append(t)
    elif out_msg.search(ln):
        m = re.search(r"Prepare to send - /([0-9A-Fa-f]+):", ln)
        key = m.group(1) if m else None
        if key and pend.get(key):
            t0 = pend[key].pop(0)
            d = t - t0
            if 0 <= d < 300:
                deltas.append(d)
    if skip.search(ln):
        skipped += 1

if not deltas:
    print("无可用样本")
    sys.exit(0)

deltas.sort()


def pct(p):
    return deltas[min(len(deltas) - 1, int(len(deltas) * p))]


print("=" * 56)
print("回复延迟分布（消息到达到回复发出）")
print("=" * 56)
print("  样本数 : %d" % len(deltas))
print("  最小   : %.1f 秒" % deltas[0])
print("  P50    : %.1f 秒" % pct(0.50))
print("  P75    : %.1f 秒" % pct(0.75))
print("  P90    : %.1f 秒" % pct(0.90))
print("  P99    : %.1f 秒" % pct(0.99))
print("  最大   : %.1f 秒" % deltas[-1])
print("  平均   : %.1f 秒" % (sum(deltas) / len(deltas)))
over = [d for d in deltas if d > 30]
print("  >30秒  : %d 次（%.0f%%）" % (len(over), 100.0 * len(over) / len(deltas)))
print("  被跳过 : %d 次" % skipped)
