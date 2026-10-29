"""检索层：把 L0 原始聊天 / L1 人物档案 / L3 日纪要 组装成能直接塞进提示词的紧凑文本。

这一层**不调模型**（0 token），只做查库 + 拼字符串。
每条输出都带时间戳和说话人，让模型知道"谁在什么时候说了什么"。
"""
import time

from .store import _day_cst

# 单次检索返回的字数上限：太多会挤占上下文，也会拖慢首字
MAX_CHARS = 2600


def _fmt_ts(ts):
    return time.strftime("%m-%d %H:%M", time.gmtime(float(ts) + 8 * 3600))


def fmt_events(rows, max_chars=MAX_CHARS, with_speaker=True):
    """聊天流水 → 文本。按时间正序，超长时保留最新的。"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: r["ts"])
    lines = []
    for r in rows:
        fields=set(r.keys())
        group=str(r['group_id'] or '') if 'group_id' in fields else ''
        if group in {'0','None'}:group=''
        session=str(r['session_id'] or '') if 'session_id' in fields else ''
        source=('群…'+group[-6:]) if group else ('私聊' if 'FriendMessage' in session or 'PrivateMessage' in session else '来源未记录')
        who = "我" if (r["role"] == "assistant" or r["is_self"]) else (r["sender_name"] or "某人")
        text = (r["content"] or "").replace("\n", " ").strip()
        if len(text) > 200:
            text = text[:200] + "…"
        if with_speaker:
            lines.append("[%s][%s] %s: %s" % (_fmt_ts(r["ts"]), source, who, text))
        else:
            lines.append("[%s][%s] %s" % (_fmt_ts(r["ts"]), source, text))
    # 从尾部往回装，超限就丢最早的（最近的更有用）
    out, total = [], 0
    for ln in reversed(lines):
        if total + len(ln) + 1 > max_chars:
            break
        out.append(ln)
        total += len(ln) + 1
    out.reverse()
    if len(out) < len(lines):
        out.insert(0, "…（更早的内容已省略）")
    return "\n".join(out)


def fmt_person(store, person, with_facts=True):
    """人物档案 → 文本。"""
    import json
    aliases = json.loads(person["aliases"] or "[]")
    aliases = [a for a in aliases if a and a != person["name"]]
    head = "%s（%d 条消息，%s ~ %s" % (
        person["name"] or person["sender_id"],
        person["msg_count"] or 0,
        _day_cst(person["first_seen"]) if person["first_seen"] else "?",
        _day_cst(person["last_seen"]) if person["last_seen"] else "?",
    )
    if aliases:
        head += "，曾用名/别名：" + "、".join(aliases[-4:])
    head += "）"
    parts = [head, '来源：跨会话人物汇总；旧画像与事实未逐条标注来源，不代表发生在当前群。']
    if person["summary"]:
        parts.append("画像：" + person["summary"])
    if with_facts:
        facts = store.get_facts(person["sender_id"], active_only=True, limit=30)
        if facts:
            bucket = {}
            for f in facts:
                bucket.setdefault(f["category"] or "其他", []).append(f["fact"])
            for cat, items in bucket.items():
                parts.append("· %s：%s" % (cat, "；".join(items)))
    return "\n".join(parts)


def recall_person(store, key, day_limit=8):
    """按名字/ID 找人，返回（档案文本, 最近发言文本）。找不到返回 (None, None)。"""
    person = store.get_person(key)
    if person is None:
        return None, None
    profile = fmt_person(store, person)
    recent = store.search_events(None, sender_id=person["sender_id"], limit=day_limit)
    return profile, fmt_events(recent, max_chars=1000)


def recall_chat(store, query=None, person=None, days=None, limit=12):
    """查原始聊天。person 可传名字/别名/ID。"""
    sender_id = None
    who = ""
    if person:
        p = store.get_person(person)
        if p is None:
            return "没找到叫「%s」的人。可以用 list_people 看看认识谁。" % person
        sender_id, who = p["sender_id"], p["name"]
    since = None
    if days:
        since = time.time() - float(days) * 86400
    rows = store.search_events(query, sender_id=sender_id, since_ts=since, limit=limit)
    if not rows:
        tip = []
        if query:
            tip.append("关键词「%s」" % query)
        if who:
            tip.append("说话人=%s" % who)
        return "没查到同时满足的聊天记录（%s）。可以放宽关键词或时间范围。" % (
            "，".join(tip) or "无过滤条件")
    body = fmt_events(rows)
    head = "查到 %d 条" % len(rows)
    if query:
        head += "（关键词：%s）" % query
    if who:
        head += "（说话人：%s）" % who
    return head + "，按时间正序：\n" + body


def list_people(store, limit=15):
    rows = store.db.execute(
        "SELECT * FROM people ORDER BY msg_count DESC LIMIT ?", (int(limit),)
    ).fetchall()
    if not rows:
        return "还没有人物数据。"
    out = ["认识的人（按发言量）："]
    for r in rows:
        last = _day_cst(r["last_seen"]) if r["last_seen"] else "?"
        tail = ("　" + r["summary"][:40]) if r["summary"] else ""
        out.append("· %s　%d 条　最近 %s%s" % (r["name"], r["msg_count"] or 0, last, tail))
    return "\n".join(out)
