#!/usr/bin/env python3
"""修客户端致命崩溃：打印含颜文字的任务文本时 UnicodeEncodeError。

现象：
    print("[任务] %s" % text[:40], flush=True)
    UnicodeEncodeError: 'gbk' codec can't encode character '\\uff9f'

原因：面板用二进制方式重定向 stdout，Python 于是按系统代码页（中文 Windows = GBK）
编码输出。任务文本里带颜文字的半角片假名（ﾟ = U+FF9F）GBK 表示不了 →
抛异常 → **整个客户端进程崩掉** → 之后服务器一直 408 → 回落 Edge（听起来"有口音"）。

修法（双保险）：
1. 启动时把 stdout/stderr 重设为 UTF-8 + errors="replace"
2. 打印动态文本改用 _log()，异常时降级 ASCII，绝不让它崩
"""
import io
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
shutil.copy(P, "%s.bak-utf8-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S")))
s = P.read_text(encoding="utf-8")

# ---------- 1) import 之后立刻重设输出编码 ----------
old_imp = "import urllib.parse\nimport urllib.request\n"
new_imp = (
    "import urllib.parse\n"
    "import urllib.request\n"
    "\n"
    "# ---- 输出编码兜底（务必放在任何 print 之前）----\n"
    "# 面板把 stdout 重定向到文件时，Python 按系统代码页（中文 Windows = GBK）编码。\n"
    "# 任务文本里只要出现颜文字的半角片假名（如 ﾟ U+FF9F），print 就会抛\n"
    "# UnicodeEncodeError 并让整个客户端进程退出 —— 之后服务器一直 408。\n"
    "for _stream in (sys.stdout, sys.stderr):\n"
    "    try:\n"
    '        _stream.reconfigure(encoding="utf-8", errors="replace")\n'
    "    except Exception:\n"
    "        pass\n"
    "\n"
    "\n"
    "def _log(msg=\"\"):\n"
    '    """安全打印：任何编码问题都不允许弄崩客户端。"""\n'
    "    try:\n"
    "        print(msg, flush=True)\n"
    "    except Exception:\n"
    "        try:\n"
    '            print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)\n'
    "        except Exception:\n"
    "            pass\n"
)
assert old_imp in s, "import 段未匹配"
s = s.replace(old_imp, new_imp, 1)

# ---------- 2) 动态文本打印统一走 _log ----------
pairs = [
    ('print("[任务] %s" % text[:40], flush=True)',
     '_log("[任务] %s" % text[:40])'),
]
n = 0
for old, new in pairs:
    if old in s:
        s = s.replace(old, new, 1)
        n += 1

# [断线] 那行：把 print( 换成 _log(，并去掉 , flush=True)
i = s.find('print("[断线]')
if i >= 0:
    j = s.find("\n", i)
    line = s[i:j]
    fixed = line.replace("print(", "_log(", 1).replace(", flush=True)", ")")
    s = s[:i] + fixed + s[j:]
    n += 1

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("客户端已修（UTF-8 输出 + _log 兜底），语法通过")
print("  替换处数:", n)
print("  含 reconfigure:", "reconfigure" in s)
print("  含 _log 定义:", "def _log(" in s)
print("  残留 print(\"[任务]\":", 'print("[任务]' in s)

# ---------- 3) 用 GBK 编码的 stdout 复现 + 验证兜底 ----------
print()
print("=== 复现验证（模拟面板的 GBK 重定向）===")
sample = "今天也要加油啊，别偷懒，我盯着你呢 (*ﾟ∇ﾟ)"

buf = io.BytesIO()
gbk_out = io.TextIOWrapper(buf, encoding="gbk", errors="strict", newline="")
real = sys.stdout
try:
    sys.stdout = gbk_out
    try:
        print(sample, flush=True)
        sys.stdout = real
        print("  !! 未复现（不符合预期）")
    except UnicodeEncodeError as e:
        sys.stdout = real
        print("  OK 原始写法确实会崩:", str(e)[:60])
finally:
    sys.stdout = real

# 再用 _log 的实现验证
ns = {}
exec(
    "import sys\n"
    "def _log(msg=''):\n"
    "    try:\n"
    "        print(msg, flush=True)\n"
    "    except Exception:\n"
    "        try:\n"
    "            print(str(msg).encode('ascii','replace').decode('ascii'), flush=True)\n"
    "        except Exception:\n"
    "            pass\n",
    ns,
)
buf2 = io.BytesIO()
gbk2 = io.TextIOWrapper(buf2, encoding="gbk", errors="strict", newline="")
try:
    sys.stdout = gbk2
    ns["_log"](sample)
    sys.stdout = real
    print("  OK _log 在 GBK 输出下不再抛异常（降级为 ASCII 替换）")
except Exception as e:
    sys.stdout = real
    print("  !! _log 仍抛异常:", type(e).__name__, str(e)[:60])
