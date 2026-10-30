"""注入片段拼装：纯函数，不依赖 astrbot，便于测试。"""

LIMITS = {"rule": 60, "term": 20, "meaning": 40, "user": 40, "bot": 60}


def _pick(value, key):
    try:
        return str(value[key] if value[key] is not None else "").strip()
    except Exception:
        return ""


def build_block(rules, jargon, shots, max_chars: int = 600) -> str:
    """把已批准的规则/黑话/示例拼成一段临时上下文；超长按类轮流丢弃。"""
    rule_lines, jargon_lines, shot_lines = [], [], []
    for row in rules or []:
        rule = _pick(row, "rule")[:LIMITS["rule"]]
        if rule:
            rule_lines.append("· 表达习惯：%s" % rule)
    for row in jargon or []:
        term = _pick(row, "term")[:LIMITS["term"]]
        meaning = _pick(row, "meaning")[:LIMITS["meaning"]]
        if term and meaning:
            jargon_lines.append("· 群内黑话：%s = %s" % (term, meaning))
    for row in shots or []:
        user_text = _pick(row, "user_text")[:LIMITS["user"]]
        bot_reply = _pick(row, "bot_reply")[:LIMITS["bot"]]
        if user_text and bot_reply:
            shot_lines.append("· 参考对话：用户「%s」→ 你「%s」" % (user_text, bot_reply))

    head = ("<style>\n以下仅是本群的表达参考：模仿语气与用词，不要复述这些条目，"
            "不要改变事实、立场与结论。\n")
    tail = "</style>"
    budget = max(0, int(max_chars) - len(head) - len(tail))

    body, used = [], 0
    # 三类轮流取，避免规则一多就把黑话/示例挤掉
    buckets = [rule_lines, jargon_lines, shot_lines]
    while any(buckets):
        for bucket in buckets:
            if not bucket:
                continue
            line = bucket.pop(0)
            if used + len(line) + 1 > budget:
                bucket.clear()
                continue
            body.append(line)
            used += len(line) + 1

    if not body:
        return ""
    return head + "\n".join(body) + "\n" + tail
