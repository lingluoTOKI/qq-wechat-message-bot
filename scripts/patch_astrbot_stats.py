"""给 AstrBot v4.29.3 打补丁：修复 ToolLoopAgentRunner.stats 未初始化

根因
----
`self.stats = AgentStats()` 只在 `reset()` 方法末尾（210 行起）初始化。
但当 LLM 请求被跳过（如预算超限，547 行 "Skipping LLM request"）时，
执行流会直接走到 875 行的 `self.stats.current_context_tokens = 0`，
此时 self.stats 尚不存在 → AttributeError → 整个 agent 崩溃，
用户收到 "Error occurred during AI execution"。

修法
----
在 875 行前插入防御性初始化：缺什么补什么，不改动任何其他逻辑。
幂等：已打过补丁则跳过（以 PATCH_MARK 标记判定）。
"""
import os
import re
import shutil
import sys
from datetime import datetime

TARGET = "/AstrBot/astrbot/core/agent/runners/tool_loop_agent_runner.py"
MARK = "# [patch] stats-not-initialized"
BACKUP_SUFFIX = ".bak-prepatch"


def already_patched(text):
    return MARK in text


def main():
    if not os.path.isfile(TARGET):
        print("[patch] 目标不存在，跳过: %s" % TARGET)
        return 0

    with open(TARGET, encoding="utf-8") as f:
        text = f.read()

    if already_patched(text):
        print("[patch] 已打过补丁，跳过")
        return 0

    # 定位 875 行那一句
    anchor = "self.stats.current_context_tokens = 0"
    if anchor not in text:
        print("[patch] 未找到锚点语句，框架版本可能已变化，跳过")
        return 0

    # 备份
    bak = TARGET + BACKUP_SUFFIX + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(TARGET, bak)

    guard = (
        "            %s\n"
        "            if not hasattr(self, 'stats'):\n"
        "                self.stats = AgentStats()\n"
        "                self.stats.start_time = time.time()\n" % MARK
    )

    # 只替换第一处（即 step() 里那处），用 count=1
    idx = text.index(anchor)
    # 往前找该行行首，插入 guard
    line_start = text.rfind("\n", 0, idx) + 1
    indent = text[line_start:idx]
    guard = guard.replace("            ", indent, 1)
    guard = (
        indent + MARK + "\n"
        + indent + "if not hasattr(self, 'stats'):\n"
        + indent + "    self.stats = AgentStats()\n"
        + indent + "    self.stats.start_time = time.time()\n"
    )
    new_text = text[:line_start] + guard + text[line_start:]

    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(new_text)

    # 语法校验
    import py_compile
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(bak, TARGET)
        print("[patch] 语法校验失败，已回滚: %s" % e)
        return 1

    print("[patch] 已应用: %s" % TARGET)
    print("[patch] 备份  : %s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
