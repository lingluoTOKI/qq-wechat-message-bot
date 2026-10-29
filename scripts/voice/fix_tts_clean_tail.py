#!/usr/bin/env python3
"""补强 _clean_for_tts：颜文字的"手臂"常留在括号外（如 （ノ≧∀≦）ノ 的最后一个ノ）。

做法：括号组删掉后，再把**首尾**的非中文/非字母数字/非常用标点字符全部裁掉。
我们的朗读内容以中文为主，这个裁剪是安全的。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/tts_api/dy_tts_api.py")
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy(P, "%s.bak-kaotail-%s" % (P, STAMP))
s = P.read_text(encoding="utf-8")

old = """    t = _PAREN_RE.sub(_repl, text)
    # 清掉残留的孤立符号串与多余空白
    t = re.sub(r"[\\s\\u3000]{2,}", " ", t).strip()
    return t"""
new = """    t = _PAREN_RE.sub(_repl, text)
    # 颜文字的"手臂"常在括号外（（ノ≧∀≦）ノ 的最后一个ノ）→ 裁掉首尾纯符号残片。
    # 保留：中文、字母数字、常用中英文标点、成对括号与引号。
    _KEEP = r"\\u4e00-\\u9fffA-Za-z0-9。！？!?，,、；;：:.…~～\\"'）)】」》"
    t = re.sub(r"[^%s]+$" % _KEEP, "", t)
    t = re.sub(r"^[^%s]+" % _KEEP, "", t)
    # 清掉多余空白
    t = re.sub(r"[\\s\\u3000]{2,}", " ", t).strip()
    return t"""
assert old in s, "清洗函数体未匹配"
s = s.replace(old, new, 1)
P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("已补强首尾裁剪，语法通过")
