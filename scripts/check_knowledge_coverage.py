"""检查知识库在现有检索逻辑下实际能被喂给模型的比例。

关注点：search_knowledge(max_chars=2500) 是否把知识库后半部分永久截断。
"""
import os
import sys

sys.path.insert(0, "/tmp")

DATA = os.environ.get("WUSHU_DATA", "/opt/bot-stack/astrbot/data/plugin_data/wushu_ops")
PLUGIN = os.environ.get("WUSHU_SRC", "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops")
MAX_CHARS = 2500

path = os.path.join(DATA, "knowledge.md")
if not os.path.exists(path):
    print("未找到 knowledge.md")
    raise SystemExit(0)

with open(path, encoding="utf-8") as f:
    text = f.read()

print("知识库总字符 : %s" % len(text))
print("当前截断上限 : %s" % MAX_CHARS)
print("可送达比例   : %.1f%%" % (min(len(text), MAX_CHARS) / len(text) * 100))
print("被永久截掉   : %s 字符" % max(0, len(text) - MAX_CHARS))

print("\n--- 各章节可达性（按累计字符位置）---")
sections = []
for p in text.split("\n## "):
    p = p.strip()
    if not p:
        continue
    lines = p.split("\n", 1)
    title = lines[0].lstrip("# ").strip()
    body = lines[1].strip() if len(lines) > 1 else ""
    sections.append((title, body))

pos = 0
for title, body in sections:
    start = pos
    end = pos + len(title) + len(body)
    reachable = end <= MAX_CHARS
    print("  %-28s %5d~%-5d  %s" % (title[:26], start, end, "✅ 可达" if reachable else "❌ 超出截断，永不送达"))
    pos = end

print("\n--- 中文字符 → token 粗估（1 token ≈ 1.5 中文字符）---")
full_tokens = len(text) / 1.5
cut_tokens = min(len(text), MAX_CHARS) / 1.5
print("  全文  : 约 %.0f token" % full_tokens)
print("  截断后: 约 %.0f token" % cut_tokens)
print("  差值  : 约 %.0f token（DeepSeek 前缀缓存命中时成本极低）" % (full_tokens - cut_tokens))
