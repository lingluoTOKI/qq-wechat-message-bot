"""生日抽取：确定性正则，0 token。

为什么单独做这一层：
  · 生日出现频率极低，但一旦说过就不该再变；
  · 靠每天一次的 LLM 提炼会被「最近 5 天 / 每人 40 条 / 裁到 3600 字」漏掉；
  · 靠注入的 8 条事实配额，又会被后来的新事实挤出（按 created_at 倒序）。
  所以这里用正则直接抽，落到 people.birthday 一列，注入时固定占一行，
  不参与事实配额竞争，也不依赖模型记得记。

只认「明确主语」：
  「你的生日是10月22日哦，我的是11月7日」里只有后一个日期算本人；
  「10月25」（别人问 bot 时给别的 bot定的）因为没有主语线索，一律不收。
"""

import re
import time
from datetime import date

# 兼容 11.7 / 11-7 / 11/7 / 11月7日 / 11月7号；前后不能粘着别的数字，
# 否则 "13月40日" 会被截成 "3月4日" 这种假命中
_DATE_BODY = (r"(?<!\d)(1[0-2]|0?[1-9])\s*(?:月|[.\-/．])\s*"
              r"(3[01]|[12]\d|0?[1-9])(?!\d)\s*(?:日|号)?")
_DATE = re.compile(_DATE_BODY)
# 本人主语线索，按优先级排列（"我的生日是11月7日" 优于 "我的是11月7日"）
_SELF_CUES = ("我的生日", "我生日", "我出生", "我生日是", "我的是")
# 「今天是我生日」这种不给日期的说法，用消息时间补
_SELF_TODAY = ("今天是我生日", "今天我生日", "我今天生日", "今日我生日", "我今日生日")

# 第三人称：别人替成员报生日（"耀白的生日是10月25""今天是千本の日光生日"）
_NAME = r"(?P<name>[\u4e00-\u9fa5\u3040-\u30ffA-Za-z0-9_.\-·]{1,16})"
_NAMED_A = re.compile(_NAME + r"\s*的?\s*生日\s*(?:是|在|为|：|:)?\s*" + _DATE_BODY)
_NAMED_B = re.compile(_DATE_BODY + r"\s*(?:是|就是|为)\s*" + _NAME + r"\s*的生日")
_NAMED_TODAY = re.compile(r"今天(?:是)?\s*" + _NAME + r"\s*(?:的)?生日")
_BLOCK_NAMES = {"我", "你", "他", "她", "它", "我们", "你们", "今天", "大肥鱼",
                "爱吃饭的大肥鱼", "bot", "机器人", "AI"}


def normalize(value):
    """任意输入 → 'X月Y日'；不合法（月份/日期越界）返回空串。"""
    if not value:
        return ""
    m = _DATE.search(str(value))
    if not m:
        return ""
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    try:
        date(2000, month, day)
    except ValueError:
        return ""
    return "%d月%d日" % (month, day)


def detect_self(text):
    """从一条发言判断「本人自报生日」。命中返回 'X月Y日'，否则空串。"""
    t = (text or "").strip()
    if not t or not any(cue in t for cue in _SELF_CUES):
        return ""
    for cue in _SELF_CUES:
        idx = t.find(cue)
        if idx < 0:
            continue
        m = _DATE.search(t, idx + len(cue))
        if m:
            value = normalize(m.group(0))
            if value:
                return value
    return ""


def detect_self_today(text):
    """「今天是我生日」——没给日期时用消息时间补，返回 True。"""
    t = (text or "").strip()
    return bool(t) and "生日" in t and any(cue in t for cue in _SELF_TODAY)


def date_from_ts(ts):
    """消息时间（UTC 时间戳）→ 北京时间的 'X月Y日'。"""
    st = time.gmtime(float(ts or 0) + 8 * 3600)
    return "%d月%d日" % (st.tm_mon, st.tm_mday)


def detect_named(text, ts=None):
    """别人替成员报生日 → [(名字, 'X月Y日')]。

    只认「A的生日是…」「…是A的生日」「今天是A生日」三种句式；
    A 必须能在 people 表里找到才落库（见 learn_from_events），避免把
    "大肥鱼生日10月25" 之类写到真人头上。
    """
    t = (text or "").strip()
    if not t or "生日" not in t:
        return []
    found = []
    for pattern in (_NAMED_A, _NAMED_B):
        for m in pattern.finditer(t):
            found.append((m.group("name").strip().rstrip("的"), normalize(m.group(0))))
    if ts:
        for m in _NAMED_TODAY.finditer(t):
            found.append((m.group("name").strip().rstrip("的"), date_from_ts(ts)))
    result, seen = [], set()
    for name, value in found:
        if not value or not name or name in _BLOCK_NAMES:
            continue
        if (name, value) in seen:
            continue
        seen.add((name, value))
        result.append((name, value))
    return result


def scan(rows, limit=200):
    """发言按时间倒序 → 最近一次自报生日，返回 (值, 所在行)。"""
    for row in rows[:limit]:
        value = detect_self(row["content"])
        if not value and detect_self_today(row["content"]):
            value = date_from_ts(row["ts"])
        if value:
            return value, row
    return "", None


def refresh(store, sender_id):
    """从归档里重算某人（store 为独立连接）的生日并写库，返回 'X月Y日' 或 ''。

    只扫命中「生日 / 出生」的本人发言，纯 SQL + 正则，成本可忽略。
    """
    rows = store.db.execute(
        "SELECT * FROM events WHERE sender_id=? AND role='user' AND is_self=0"
        " AND (content LIKE '%生日%' OR content LIKE '%出生%') ORDER BY ts DESC LIMIT 200",
        (sender_id,),
    ).fetchall()
    value, _ = scan(rows)
    if not value:
        return ""
    if value != (store.get_birthday(sender_id) or ""):
        store.set_birthday(sender_id, value)
    return value


def learn_from_events(store, days=365, limit=2000):
    """扫归档里"别人替成员报生日"的发言，补全还没记录的人。

    这是「自动记住群内所有人生日」的兜底：不是每个人都会说"我的生日是…"，
    但群里常有人说"XX 的生日是 10月25"。只填空缺，不覆盖本人自报。
    """
    cutoff = time.time() - days * 86400
    rows = store.db.execute(
        "SELECT ts, content FROM events WHERE content LIKE '%生日%' AND ts>=?"
        " ORDER BY ts DESC LIMIT ?", (cutoff, int(limit))
    ).fetchall()
    filled = 0
    for row in rows:
        for name, value in detect_named(row["content"], row["ts"]):
            person = store.db.execute(
                "SELECT sender_id, birthday FROM people WHERE name=? LIMIT 1", (name,)
            ).fetchone()
            if person and not (person["birthday"] or "").strip():
                store.set_birthday(person["sender_id"], value)
                filled += 1
    return filled
