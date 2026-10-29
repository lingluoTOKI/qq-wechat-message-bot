#!/usr/bin/env python3
"""合成前清掉「不适合朗读的内容」（主要是颜文字）。

问题：kaomoji 插件会把 {{分类}} 替换成真颜文字贴在句尾，
clonetts 拿到的是「今天也要加油啊… (*ﾟ∇ﾟ)」，整段直接送进 TTS →
TTS 会把括号和符号念出来/念成怪音，听起来就是"什么鬼"。

做法：去掉**括号内不含中文**的括号组（颜文字的典型特征），
保留含中文的括号（那是正常说明，如「（笑）」「（新版本）」）。
三个 TTS 通道（本机/MiMo/Edge）入口统一处理，一处生效三层都干净。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/tts_api/dy_tts_api.py")
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy(P, "%s.bak-kaoclean-%s" % (P, STAMP))
s = P.read_text(encoding="utf-8")

# 1) 插入清洗函数（放在 _pick_emotion_tag 之前，保证 re 已导入）
anchor = "def _pick_emotion_tag(text):"
assert anchor in s, "锚点未找到"

helper = '''_PAREN_RE = re.compile(r"[（(][^）)]{0,40}[）)]")


def _clean_for_tts(text):
    """去掉不适合朗读的内容（颜文字等），避免被 TTS 念出来。

    规则：括号组内**没有中文**就整体删掉（颜文字如 (*ﾟ∇ﾟ)、(╯°□°）╯ 都属此类）；
    含中文的括号保留（如「（笑）」是正常语义）。
    """
    if not text:
        return ""

    def _repl(m):
        inner = m.group(0)[1:-1]
        return m.group(0) if re.search(r"[\\u4e00-\\u9fff]", inner) else ""

    t = _PAREN_RE.sub(_repl, text)
    # 清掉残留的孤立符号串与多余空白
    t = re.sub(r"[\\s\\u3000]{2,}", " ", t).strip()
    return t


'''
s = s.replace(anchor, helper + anchor, 1)

# 2) 在入口统一清洗
old = """async def tts_http_stream(self, text, context_texts):
    _vt = str(getattr(self, 'voice_type', '') or '')
"""
new = """async def tts_http_stream(self, text, context_texts):
    _vt = str(getattr(self, 'voice_type', '') or '')

    # 统一清洗：颜文字/符号不该被念出来（三个通道共用这一点）
    _cleaned = _clean_for_tts(text)
    if _cleaned != (text or ""):
        logger.debug(f'TTS 清洗：{text!r} -> {_cleaned!r}')
    text = _cleaned
    if not text:
        logger.warning('TTS 清洗后为空，跳过本次语音')
        return None
"""
assert old in s, "入口未匹配"
s = s.replace(old, new, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("已插入 _clean_for_tts 并在入口生效，语法通过")

# 自检（纯函数行为）
import importlib.util
import re as _re
import sys
import types

# 用最简桩件加载该模块的清洗函数逻辑做验证
test_cases = [
    ("今天也要加油啊，别偷懒，我盯着你呢 (*ﾟ∇ﾟ)", "今天也要加油啊，别偷懒，我盯着你呢"),
    ("好耶（ノ≧∀≦）ノ", "好耶"),
    ("这次更新了（新版本）", "这次更新了（新版本）"),
    ("嗯", "嗯"),
]
src = P.read_text(encoding="utf-8")
m = _re.search(r"_PAREN_RE = re\.compile\((.*?)\)\n\n\ndef _clean_for_tts", src, _re.S)
ns = {"re": _re}
exec("import re\n_PAREN_RE = " + m.group(1), ns)
exec(src[src.index("def _clean_for_tts"): src.index("def _pick_emotion_tag")], ns)
f = ns["_clean_for_tts"]
print()
print("=== 清洗自检 ===")
ok = True
for raw, want in test_cases:
    got = f(raw)
    flag = "OK" if got == want else "!!"
    if got != want:
        ok = False
    print("  %s %r -> %r" % (flag, raw, got))
print("结果:", "全部符合预期" if ok else "有用例不符")
