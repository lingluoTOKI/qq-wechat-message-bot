"""L1 提炼器：把某个人的聊天记录交给旁路小模型，抽出「画像 + 事实」。

跟 livingmemory 的区别：
  · livingmemory 抽的是**事件/对话**级别的记忆（"他们说周末去漫展"）
  · 这里抽的是**人**的属性（"耀白是学生，喜欢《哭泣少女乐队》，凌晨活跃"）
  两者互补：一个是"发生了什么"，一个是"这个人是谁"。

约束（改 prompt 前先读）：
  · 只写聊天里真的有依据的，宁可少写也不编
  · 别人的评价也算这个人的信息，但要标明是别人说的
  · 已有事实一起喂进去，让模型判断"重复/更新/新增"，避免每次全量重复追加
  · 输出必须是纯 JSON，解析失败就整批丢弃，不猜
"""
import json
import re
import time

CATEGORIES = ["身份", "喜好", "关系", "经历", "习惯", "雷点"]

SYSTEM = """你在维护一个 QQ 群成员的人物档案。给你的是这个人在群里的发言（可能还有别人对他的评价）。

请输出**严格的 JSON**，不要 markdown 代码块，不要任何解释文字。格式：
{"summary":"一句话画像，30字以内，第三人称","birthday":"","facts":[{"category":"分类","fact":"一句话事实","confidence":0.9}]}

抽取规则：
1. summary：这个人是个什么样的人。只写有依据的特征，不要客套话，不要"是一个群成员"这种废话。
2. facts：每条一个独立事实，一句话说完，不带主语（写"喜欢《哭泣少女乐队》"而不是"他喜欢…"）。
3. category 只能取：身份 / 喜好 / 关系 / 经历 / 习惯 / 雷点
   - 身份：学生/上班/在哪个城市/玩什么游戏
   - 喜好：喜欢的作品、食物、音乐、玩法
   - 关系：跟群里谁关系好/是什么关系/怎么称呼别人
   - 经历：发生过的事、"上次说要去…"、计划
   - 习惯：作息、说话方式、口头禅、常用表情
   - 雷点：明确表示过讨厌/不想提的东西
4. confidence：0.9 表示本人原话明确说过；0.7 表示能较稳地推断；低于 0.6 不要写。
5. **只写有依据的**。没有信息就返回 {"summary":"","facts":[]}，绝不编造。
6. **不要记录关于「大肥鱼」的信息**。「大肥鱼」是群里的机器人，不是这个人。
   如果聊天里出现"我的生日是X""我喜欢Y"，要看清是**谁**说的：
   只有「本人」说的才算这个人的信息；「大肥鱼」说的（例如"我的生日在七月"）
   **绝对不能**写成这个人的事实。别人说的关于这个人的话才可以采信。
6b. 形如「[引用: 某句话] 本人的回复」是本人**在回应那句话**。
   引用块里的内容可能是大肥鱼说的、也可能是别人说的 ——
   只有当回复明确表示"我自己如何"时才归到本人。
   反例：大肥鱼说"生日呀，我自己没特意记过"，本人回"10月25" →
   这是本人在**给大肥鱼定生日**，不是本人的生日，不能写成"生日是10月25日"。
7. 如果"已知事实"里已有相同信息，不要重复输出；如果新信息**推翻了**旧事实，
   在 fact 里直接写新的，并在 "supersedes" 字段给出旧事实的序号（从 0 开始）。
   例：{"category":"身份","fact":"已经工作了，在做游戏开发","confidence":0.9,"supersedes":[2]}
8. 最多 8 条 facts。挑最有信息量的。**身份类（生日/职业/城市）优先占位**——
   这类信息一旦丢失后面很难补回来，喜好类可以少写。
9. 生日单独填 "birthday" 字段：本人明确说过完整日期时写 "X月Y日"（例："11月7日"），
   并同时输出一条 {"category":"身份","fact":"生日是11月7日","confidence":0.95}。
   "你的生日是…"是在给机器人/别人定生日，不算本人；不确定是本人就留空字符串。
   「爱吃饭的大肥鱼」「大肥鱼」都是机器人，它们的生日永远不算本人。"""


_PLACEHOLDER = re.compile(r"^(?:\s*\[(?:表情|图片|语音|视频|文件|闪照)\]\s*)+$")
# 我们自己调试机器人时的技术输出，对"这个人是什么样的人"零信息量，直接丢
_DIAG = re.compile(
    r"^(?:\[?(?:CacheGuard|ContextBudget|Conversation Token)"
    r"|📊|📈|💴|🏆|🔧|\[CacheGuard\]|\[TokenBudget\])"
)
# 本人敲的调试命令，也不是"人"的信息
_CMD = re.compile(r"^[/／](?:tk|stats|ctxbudget|ping|帮助|help)\b|^(?:ctxbudget|缓存状态|感受一下插件优化)$",
                  re.I)


def _clean(text):
    """去掉 QQ 的 at 标记 / CQ 码 / 纯占位，再过滤调试内容。

    顺序很重要：必须**先剥离 `[At:xxx]` 这类前缀**，再判断是不是调试命令。
    否则 `[At:qq_official] ctxbudget` 会因为开头是 `[` 而躲过命令过滤。
    """
    t = (text or "").strip()
    t = re.sub(r"<@!?[0-9A-Fa-f]+>", "", t)
    t = re.sub(r"\[CQ:[^\]]*\]", "", t)
    t = re.sub(r"\[At:[^\]]*\]", "", t)
    t = re.sub(r"\[(?:表情|图片|语音|视频|文件|闪照)\]", "", t)
    t = re.sub(r"<attachmentType=[^>]*>", "", t)
    t = t.strip()
    if not t:
        return ""
    if _DIAG.search(t) or _CMD.search(t):
        return ""
    return t


def gather_context(store, person, k_mine=40, before=120, after=60, max_rows=300,
                   per_day=15):
    """取"本人若干条发言 + 前后各一小段别人的话"。

    两个坑，都在这里处理：
      1. 只取本人发言不行：群里大量是"打不打"/"来"/"码"这种碎片，
         脱离上下文完全无法判断在说什么，抽出来全是废事实 → 必须带上下文。
      2. 只取最近的发言也不行：这个人可能今天凌晨刷了 40 条，
         那样就只看得到一晚上的事 → **按天分散采样**，每天最多 per_day 条。
    """
    sid = person["sender_id"]
    all_mine = store.db.execute(
        "SELECT * FROM events WHERE sender_id=? AND role='user' AND is_self=0"
        " ORDER BY ts DESC", (sid,)
    ).fetchall()
    if not all_mine:
        return []

    by_day = {}
    for m in all_mine:
        d = m["day"]
        by_day.setdefault(d, [])
        if len(by_day[d]) < int(per_day):
            by_day[d].append(m)
    # 天数太多时只留最近的若干天，避免采样点过多
    days = sorted(by_day)[-5:]
    mine = [m for d in days for m in by_day[d]][:int(k_mine) * 3]
    mine.sort(key=lambda r: r["ts"])

    picked = {}
    for m in mine:
        ctx = store.db.execute(
            "SELECT * FROM events WHERE session_id=? AND ts BETWEEN ? AND ?"
            " ORDER BY ts ASC",
            (m["session_id"], float(m["ts"]) - before, float(m["ts"]) + after),
        ).fetchall()
        for c in ctx:
            picked[c["id"]] = c
        if len(picked) >= max_rows:
            break
    rows = sorted(picked.values(), key=lambda r: r["ts"])
    return rows


def _collapse_runs(lines, keep_head=2, keep_tail=1, run_limit=3):
    """同一个人连续刷屏时压缩。

    群里很常见"打不打 / 来 / 码 / 人呢 / 快点"这种连发，它几乎不带信息，
    却会把别人说的话挤到区间外（于是模型只看到一个人在自言自语）。
    规则：同一人的连续发言超过 run_limit 条时，只留前 keep_head 条 + 末 keep_tail 条。
    """
    out, i, n = [], 0, len(lines)
    while i < n:
        who = lines[i][0]
        j = i
        while j < n and lines[j][0] == who:
            j += 1
        run = lines[i:j]
        if len(run) <= run_limit:
            out.extend(run)
        else:
            out.extend(run[:keep_head])
            out.append((who, "…（连发 %d 条，略）" % (len(run) - keep_head - keep_tail)))
            out.extend(run[-keep_tail:])
        i = j
    return out


def _clip(events, max_chars=3600, me_id=None):
    """把事件拼成文本；超长时保留**最早 + 最近**，中间均匀丢弃。

    只留最近的会丢掉"最初印象"（比如一开始自我介绍），
    只留最早的又看不到近况，所以两头都留。
    """
    lines = []
    for r in events:
        text = _clean(r["content"])
        text = text.replace("\n", " ").strip()
        if not text or _PLACEHOLDER.match(text):
            continue
        lim = 120 if r["role"] != "user" else 180
        if len(text) > lim:
            text = text[:lim] + "…"
        d = time.strftime("%m-%d", time.gmtime(float(r["ts"]) + 8 * 3600))
        hh = time.strftime("%H:%M", time.gmtime(float(r["ts"]) + 8 * 3600))
        if r["role"] != "user" or r["is_self"]:
            who = "大肥鱼"
        elif me_id and r["sender_id"] == me_id:
            who = "本人"
        else:
            who = r["sender_name"] or "别人"
        lines.append((who, "[%s %s] %s: %s" % (d, hh, who, text)))

    if not lines:
        return ""
    lines = [ln for _, ln in _collapse_runs(lines)]

    if sum(len(x) + 1 for x in lines) <= max_chars:
        return "\n".join(lines)

    # 超长：保留前 25% 和后 60% 的条数，中间省略
    n = len(lines)
    head_n, tail_n = max(3, int(n * 0.25)), max(6, int(n * 0.60))
    if head_n + tail_n >= n:
        return "\n".join(lines[:max_chars // 40])
    body = lines[:head_n] + ["…（中间 %d 条省略）" % (n - head_n - tail_n)] + lines[-tail_n:]
    out, total = [], 0
    for ln in body:
        total += len(ln) + 1
        if total > max_chars:
            break
        out.append(ln)
    return "\n".join(out)


def build_prompt(person_name, events, existing_facts, n_messages=None, me_id=None,
                 key_lines=None):
    parts = ["【这个人】%s" % person_name]
    parts.append("（下面聊天里「本人」= %s，「大肥鱼」= 群里的机器人，其余是群友。"
                 "别人对本人说的话也是重要线索。）" % person_name)
    if existing_facts:
        parts.append("\n【已知事实（按序号，用于判断重复/更新）】")
        for i, f in enumerate(existing_facts):
            parts.append("%d. [%s] %s" % (i, f["category"] or "其他", f["fact"]))
    if key_lines:
        # 生日这类信息可能被 _clip 的头部/尾部裁剪丢掉，所以单独再列一遍
        parts.append("\n【本人明确说过的生日/出生相关原话（务必判断主语是谁）】")
        parts.extend(key_lines)
    parts.append("\n【聊天记录】")
    parts.append(_clip(events, me_id=me_id))
    if n_messages:
        parts.append("\n（以上是该成员近期 %d 条发言及其上下文节选）" % n_messages)
    return "\n".join(parts)


def parse_json(text):
    """从模型输出里抠出 JSON。容忍代码块包裹、前后废话、以及**被截断**。

    截断兜底为什么重要：模型输出有 token 上限，写到第 7 条事实时被切断，
    结果是 `{"summary":"…","facts":[{"category":"喜好","fact":"…` 这种半截内容。
    直接 json.loads 必失败，整批白跑。这里的做法是从末尾往前找最后一个
    完整的 `}`，补上未闭合的 `]` `}` 再试 —— 能救回大部分被切的结果。
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i < 0:
        return None
    # 完整闭合的候选
    if j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            pass
    # 截断修复：从后往前试每个 } 作为结尾
    body = t[i:]
    for idx in range(len(body) - 1, 0, -1):
        if body[idx] != "}":
            continue
        head = body[:idx + 1]
        for suffix in ("]}", "}", "}]}"):
            try:
                return json.loads(head + suffix)
            except Exception:
                continue
    return None


async def extract_for_person(context, store, person, provider_id, k_mine=40,
                             force=False):
    """给一个人抽事实。返回 (新增条数, 画像是否更新, 错误)。

    增量：extract_ts 之后没有新发言且已有画像时直接跳过（省 token）。
    """
    sid = person["sender_id"]
    last = store.db.execute(
        "SELECT MAX(ts) t FROM events WHERE sender_id=? AND role='user'", (sid,)
    ).fetchone()
    last_ts = float((last["t"] if last else 0) or 0)
    if last_ts <= 0:
        return 0, False, "没有该成员的发言"
    try:
        done_ts = float(person["extract_ts"] or 0)
    except Exception:
        done_ts = 0.0
    if not force and person["summary"] and last_ts <= done_ts:
        return 0, False, ""  # 无新内容，跳过

    events = gather_context(store, person, k_mine=k_mine)
    if not events:
        return 0, False, "没有该成员的发言"

    existing = store.get_facts(sid, active_only=True, limit=40)
    # 生日/出生相关原话单独再喂一遍：它很可能落在 _clip 被裁掉的中间段
    key_rows = store.db.execute(
        "SELECT * FROM events WHERE sender_id=? AND role='user' AND is_self=0"
        " AND (content LIKE '%生日%' OR content LIKE '%出生%') ORDER BY ts DESC LIMIT 20",
        (sid,),
    ).fetchall()
    key_lines = []
    for row in key_rows:
        text = _clean(row["content"]).replace("\n", " ").strip()
        if not text:
            continue
        when = time.strftime("%m-%d %H:%M", time.gmtime(float(row["ts"]) + 8 * 3600))
        key_lines.append("[%s] 本人: %s" % (when, text[:120]))
    prompt = build_prompt(person["name"], events, existing, me_id=sid,
                          key_lines=key_lines)

    from .llm_client import ask
    try:
        raw = await ask(context, provider_id, SYSTEM, prompt)
    except Exception as e:
        return 0, False, "模型调用失败：%s" % str(e)[:150]

    data = parse_json(raw)
    if not isinstance(data, dict):
        return 0, False, "模型输出不是 JSON：%s" % (raw or "")[:120]

    added = 0
    for item in (data.get("facts") or [])[:12]:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact") or "").strip()
        if not fact or len(fact) > 120:
            continue
        cat = str(item.get("category") or "其他").strip()
        if cat not in CATEGORIES:
            cat = "其他"
        try:
            conf = float(item.get("confidence") or 0.8)
        except Exception:
            conf = 0.8
        if conf < 0.6:
            continue
        # 去重：跟已有事实字面高度重合就跳过
        if any(fact in f["fact"] or f["fact"] in fact for f in existing):
            continue
        sup = []
        for idx in (item.get("supersedes") or []):
            try:
                idx = int(idx)
            except Exception:
                continue
            if 0 <= idx < len(existing):
                sup.append(existing[idx]["id"])
        context_sources=sorted({str(row['session_id']) for row in events if row['session_id']})
        source_ref=json.dumps({'provider':provider_id,'context_sources':context_sources[:8],
                               'attribution':'context_scope_only_not_exact_fact_origin'},ensure_ascii=False)
        store.add_fact(sid, fact, cat, conf, source="llm", source_ref=source_ref,
                       supersedes=sup)
        added += 1

    # 生日落独立列：即使模型没写进 facts，也不会被 8 条配额挤掉
    from .birthday import normalize as normalize_birthday
    birthday = normalize_birthday(data.get("birthday"))
    if not birthday:
        for row in store.get_facts(sid, active_only=True, limit=12):
            if "生日" in (row["fact"] or ""):
                birthday = normalize_birthday(row["fact"])
                if birthday:
                    break
    if birthday:
        store.set_birthday(sid, birthday)

    summary = str(data.get("summary") or "").strip()
    changed = False
    if summary and len(summary) <= 80 and summary != (person["summary"] or ""):
        store.db.execute(
            "UPDATE people SET summary=?, summary_at=?, updated_at=? WHERE sender_id=?",
            (summary, time.time(), time.time(), sid),
        )
        changed = True
    store.db.execute("UPDATE people SET extract_ts=? WHERE sender_id=?", (time.time(), sid))
    store.commit()
    return added, changed, ""
