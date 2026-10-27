#!/usr/bin/env python3
"""量出 system prompt 各组成部分的体积，估算 token。

在容器内运行，直接调用框架真实函数，不用猜。
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/AstrBot")


def est_tokens(s):
    """粗估 token：中文约 1 字 1 token，英文约 4 字符 1 token。"""
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    other = len(s) - cjk
    return int(cjk + other / 3.5)


def row(name, text):
    t = est_tokens(text)
    print("  %-34s %6d 字符  ~%5d token" % (name, len(text or ""), t))
    return t


def main():
    total = 0

    # 1. Safe Mode
    try:
        from astrbot.core.astr_main_agent_resources import (
            LLM_SAFETY_MODE_SYSTEM_PROMPT as SM,
        )
        total += row("Safe Mode 提示词", SM)
    except Exception as e:
        print("  Safe Mode 读取失败:", e)

    # 2. 人格 prompt
    c = sqlite3.connect("file:/AstrBot/data/data_v4.db?mode=ro", uri=True, timeout=5)
    for pid, sp, tools, skills in c.execute(
        "select persona_id, system_prompt, tools, skills from personas"
    ):
        total += row("人格 %s" % pid, sp or "")
        # 注意：tools/skills 存的是 JSON 字符串，可能是 "null"
        def _parse(v):
            try:
                return json.loads(v) if v else None
            except Exception:
                return None

        tlist, slist = _parse(tools), _parse(skills)
        if tlist:
            print("       tools 白名单: %d 个" % len(tlist))
        else:
            print("       tools = NULL → 注入全部工具集")
        if slist is None:
            print("       skills = NULL → 注入全部内置技能")
        else:
            print("       skills = %s → 只注入白名单" % (slist,))
    c.close()

    # 3. Skills 块（当前会注入 4 个）
    print()
    print("=== Skills 块 ===")
    try:
        from astrbot.core.skills import SkillManager, build_skills_prompt

        sm = SkillManager()
        skills = sm.list_skills(active_only=True, runtime="none")
        print("  作用域内技能数: %d" % len(skills))
        for s in skills:
            print("    - %-18s %s" % (s.name, (s.description or "")[:60]))
        block = build_skills_prompt(skills)
        total += row("Skills 块合计", block)
        extra = (
            "User has not enabled the Computer Use feature. You cannot use "
            "shell or Python to perform skills. If you need to use these "
            "capabilities, ask the user to enable Computer Use in the "
            "AstrBot WebUI -> Config."
        )
        total += row("Computer Use 免责说明", extra)
    except Exception as e:
        print("  Skills 读取失败:", type(e).__name__, e)

    print()
    print("=== 合计（不含 tools schema） ~%d token ===" % total)


if __name__ == "__main__":
    main()
