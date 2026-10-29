#!/usr/bin/env python3
"""验证 _clean_for_tts 的实际行为（直接从文件里 exec 真实代码，不重写逻辑）。"""
import re

P = "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/tts_api/dy_tts_api.py"
src = open(P, encoding="utf-8").read()

# 取出真实的两段源码：_PAREN_RE 定义行 + _clean_for_tts 函数体
start = src.index("_PAREN_RE = re.compile")
end = src.index("def _pick_emotion_tag")
code = "import re\n" + src[start:end]

ns = {}
exec(compile(code, "<clean>", "exec"), ns)
f = ns["_clean_for_tts"]

cases = [
    ("今天也要加油啊，别偷懒，我盯着你呢 (*ﾟ∇ﾟ)", "今天也要加油啊，别偷懒，我盯着你呢"),
    ("好耶（ノ≧∀≦）ノ", "好耶"),
    ("这次更新了（新版本）", "这次更新了（新版本）"),
    ("（笑）", "（笑）"),
    ("嗯", "嗯"),
    ("", ""),
]

print("=== _clean_for_tts 自检 ===")
ok = True
for raw, want in cases:
    got = f(raw)
    good = (got == want)
    ok = ok and good
    print("  %s %-32r -> %r" % ("OK" if good else "!!", raw, got))
print()
print("结果:", "全部符合预期" if ok else "有用例不符，需检查")
