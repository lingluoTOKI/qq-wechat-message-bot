"""大肥鱼长期记忆（A 方案）· 数据层

设计原则（改代码前先读）：
  · **完全独立**：自己的 sqlite 文件，不碰 livingmemory 的 livingmemory.db / conversations.db
    写权限（对 conversations.db 只 open 成 read-only）。
  · **L0 只归档不总结**：群里的每一句话都原样留下，用户要看的是"聊天记录"本身，
    不是被压缩过的摘要。这一层 0 token。
  · **L1 只在有冲突时追加**：人物事实走时间线，旧事实不删，靠 superseded_by 标记失效。
  · **FTS 用 bigram 手工切**：SQLite 内置 unicode61 会把整句中文当一个 token，
    所以写入时把中文切成二字组（"耀白喜欢唱歌" → "耀白 白喜 喜欢 欢唱 唱歌"），
    查询时同样切，靠隐式 AND 命中。不依赖 jieba，也不需要额外索引插件。

表：
  events       L0 原始聊天流水（永久，去重靠 msg_key）
  events_fts   L0 的全文索引（rowid 对齐 events.id）
  people       L1 人物主档（一人一行，别名、计数、一句话画像）
  facts        L1 人物事实时间线（只追加）
  digests      L3 日纪要（压缩层，可选）
  meta         KV：同步水位等
"""
import hashlib
import json
import os
import re
import sqlite3
import time

# 中文/日文假名连续段 → 切 bigram；英文数字连续段 → 原词
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff"
_SEG_RE = re.compile(r"[%s]+|[A-Za-z0-9_]+" % _CJK)


def seg_text(text: str) -> str:
    """把一段文本切成 FTS 用的检索单元串（中文 bigram + 英文原词）。"""
    if not text:
        return ""
    out = []
    for m in _SEG_RE.finditer(text):
        s = m.group(0)
        if re.fullmatch(r"[A-Za-z0-9_]+", s):
            out.append(s.lower())
            continue
        if len(s) == 1:
            out.append(s)
            continue
        for i in range(len(s) - 1):
            out.append(s[i:i + 2])
    return " ".join(out)


def query_seg(q: str, mode: str = "and") -> str:
    """查询串切分：返回 FTS MATCH 表达式。

    mode="and"：所有 bigram 都要命中。精确，但会漏 ——
               问"周末去漫展"时原文若是"周末有漫展吗"，"末去"这个组合不存在，直接漏掉。
    mode="or" ：任一命中即可，靠 bm25 相关度排序把最像的排前面（宽松兜底）。
    """
    units = seg_text(q).split()
    if not units:
        return ""
    seen, uniq = set(), []
    for u in units:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    joiner = " OR " if mode == "or" else " AND "
    return joiner.join('"%s"' % u.replace('"', '') for u in uniq)


def _day_cst(ts: float) -> str:
    """UTC 时间戳 → 北京日期字符串（容器 TZ 不可靠，这里硬算 +8）。"""
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts) + 8 * 3600))


def msg_key(session_id: str, ts: float, sender_id: str, content: str) -> str:
    raw = "%s|%.3f|%s|%s" % (session_id, float(ts), sender_id or "", content or "")
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:24]


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_key     TEXT UNIQUE NOT NULL,
    ts          REAL NOT NULL,
    day         TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    group_id    TEXT,
    sender_id   TEXT,
    sender_name TEXT,
    role        TEXT,
    content     TEXT,
    is_self     INTEGER DEFAULT 0,
    archived_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_day    ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_sender ON events(sender_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(seg, tokenize='unicode61');

CREATE TABLE IF NOT EXISTS people (
    sender_id  TEXT PRIMARY KEY,
    name       TEXT,
    aliases    TEXT DEFAULT '[]',
    first_seen REAL,
    last_seen  REAL,
    msg_count  INTEGER DEFAULT 0,
    summary    TEXT,
    summary_at REAL,
    extract_ts REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_people_last ON people(last_seen DESC);

CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id      TEXT NOT NULL,
    category       TEXT,
    fact           TEXT NOT NULL,
    confidence     REAL DEFAULT 0.8,
    source         TEXT,
    source_ref     TEXT,
    created_at     REAL NOT NULL,
    superseded_by  INTEGER DEFAULT NULL,
    superseded_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_facts_p ON facts(sender_id, superseded_by, created_at DESC);

CREATE TABLE IF NOT EXISTS digests (
    day         TEXT PRIMARY KEY,
    digest      TEXT,
    highlights  TEXT,
    participants TEXT,
    msg_count   INTEGER,
    created_at  REAL
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self):
        """老库补列。SQLite 没有 ADD COLUMN IF NOT EXISTS，只能先查再加。"""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(people)")}
        for name, ddl in (("extract_ts", "ALTER TABLE people ADD COLUMN extract_ts REAL"),):
            if name not in cols:
                try:
                    self.db.execute(ddl)
                except Exception:
                    pass
        ecols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "is_self" not in ecols:
            try:
                self.db.execute("ALTER TABLE events ADD COLUMN is_self INTEGER DEFAULT 0")
            except Exception:
                pass

    def clone(self):
        """开一个同库的独立连接。

        为什么需要：后台同步跑在 asyncio.to_thread 的工作线程里，
        而命令/工具调用在主线程。python 的 sqlite3 连接不是线程安全的
        （check_same_thread=False 只是关掉了检查，并不让它变安全），
        所以线程里一律用自己的一份连接，靠 SQLite 的 WAL 做并发。
        """
        return Store(self.path)

    # ---------- meta ----------
    def get_meta(self, k, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k, v):
        self.db.execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, str(v)),
        )
        self.db.commit()

    # ---------- L0 ----------
    def add_event(self, ts, session_id, group_id, sender_id, sender_name,
                  role, content, is_self=False) -> bool:
        """写入一条原始聊天。返回 True 表示是新增（未重复）。"""
        key = msg_key(session_id, ts, sender_id, content)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events"
            "(msg_key,ts,day,session_id,group_id,sender_id,sender_name,role,content,is_self,archived_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (key, float(ts), _day_cst(ts), session_id, group_id, sender_id,
             sender_name, role, content, 1 if is_self else 0, time.time()),
        )
        if cur.rowcount <= 0:
            return False
        rid = cur.lastrowid
        self.db.execute("INSERT INTO events_fts(rowid, seg) VALUES(?,?)",
                        (rid, seg_text(content)))
        return True

    # bot 自己的消息特征：这些只可能是她自己发出来的
    # 通义：颜文字标记、CacheGuard/ContextBudget 诊断块、QQ 图片伪标签
    BOT_MARKERS = ("{{", "CacheGuard", "ContextBudget", "attachmentType=",
                   "📊 Conversation Token", "[CacheGuard]")
    # 框架给"自己"的占位 ID
    SELF_PLACEHOLDERS = ("unknown_selfid", "qq_official", "")

    def detect_self_ids(self, min_hits=3, ratio=0.15):
        """自动找出"哪个 sender_id 其实是大肥鱼自己"。

        为什么需要这一步：QQ 官方机器人发的消息，一部分会被框架记成 role='user'，
        sender_id 是真实的 QQ 号、sender_name 是"爱吃饭的大肥鱼"。
        如果不识别出来：
          · 会给她自己建一份人物档案
          · 会把"她说过的话"当成群友的发言喂给模型，人物画像直接跑偏

        判据：一个 ID 的消息里，命中 bot 特征标记的比例明显偏高。
        这比写死 QQ 号可靠 —— 换号、换框架占位符都还能认出来。
        """
        cond = " OR ".join(["content LIKE ?" for _ in self.BOT_MARKERS])
        args = ["%" + m + "%" for m in self.BOT_MARKERS]
        found = []
        rows = self.db.execute(
            "SELECT sender_id, COUNT(*) total, SUM(CASE WHEN (%s) THEN 1 ELSE 0 END) hits"
            " FROM events WHERE role='user' AND sender_id IS NOT NULL GROUP BY sender_id"
            % cond, args,
        ).fetchall()
        for r in rows:
            total = r["total"] or 0
            hits = r["hits"] or 0
            if total >= 3 and hits >= min_hits and hits / total >= ratio:
                found.append(r["sender_id"])
        # 占位符直接算自己（哪怕没命中特征）
        for sid in self.SELF_PLACEHOLDERS:
            if sid:
                found.append(sid)
        found = sorted(set(found))
        # 回填：role='assistant' 的一律是自己；上面识别出的 ID 也标自己
        self.db.execute("UPDATE events SET is_self=1 WHERE role='assistant'")
        if found:
            ph = ",".join("?" for _ in found)
            self.db.execute("UPDATE events SET is_self=1 WHERE sender_id IN (%s)" % ph, found)
        self.db.commit()
        self.set_meta("self_ids", json.dumps(found, ensure_ascii=False))
        return found

    def commit(self):
        self.db.commit()

    def count_events(self):
        return self.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]

    def search_events(self, query, sender_id=None, day=None,
                      since_ts=None, limit=12):
        """全文检索原始聊天。query 可为空（此时按人/时间过滤即可）。

        两段式检索（这是检索质量的关键）：
          1) 先按 AND 查 —— 所有 bigram 都命中，结果精准
          2) 命中为空时才退到 OR + bm25 排序 —— 宽松召回，按相关度排
        只做 AND 会漏（"周末去漫展" vs 原文"周末有漫展吗"），
        只做 OR 会把"的""了"这种高频组合全捞进来。
        """
        def _run(mode):
            args = []
            expr = query_seg(query or "", mode)
            if expr:
                base = ("SELECT e.* FROM events_fts f JOIN events e ON e.id=f.rowid "
                        "WHERE events_fts MATCH ?")
                args.append(expr)
                order = " ORDER BY bm25(events_fts) ASC" if mode == "or" else " ORDER BY e.ts DESC"
            else:
                base = "SELECT e.* FROM events e WHERE 1=1"
                order = " ORDER BY e.ts DESC"
            if sender_id:
                base += " AND e.sender_id=?"
                args.append(sender_id)
            if day:
                base += " AND e.day=?"
                args.append(day)
            if since_ts:
                base += " AND e.ts>=?"
                args.append(float(since_ts))
            args.append(int(limit))
            return self.db.execute(base + order + " LIMIT ?", args).fetchall()

        if query:
            rows = _run("and")
            if rows:
                return rows
            return _run("or")
        return _run("and")

    # ---------- L1 ----------
    def upsert_person(self, sender_id, name=None, ts=None):
        row = self.db.execute("SELECT * FROM people WHERE sender_id=?", (sender_id,)).fetchone()
        now = time.time()
        if row is None:
            self.db.execute(
                "INSERT INTO people(sender_id,name,aliases,first_seen,last_seen,msg_count,updated_at)"
                " VALUES(?,?,?,?,?,1,?)",
                (sender_id, name, json.dumps([name] if name else [], ensure_ascii=False),
                 ts or now, ts or now, now),
            )
            return
        aliases = json.loads(row["aliases"] or "[]")
        if name and name != row["name"]:
            # 名字变了（改昵称）：旧名留作别名，便于按旧名找回
            if row["name"] and row["name"] not in aliases:
                aliases.append(row["name"])
            if name not in aliases:
                aliases.append(name)
        self.db.execute(
            "UPDATE people SET name=?, aliases=?, first_seen=MIN(COALESCE(first_seen,?),?),"
            " last_seen=MAX(COALESCE(last_seen,0),?), msg_count=msg_count+1, updated_at=? WHERE sender_id=?",
            (name or row["name"], json.dumps(aliases[-8:], ensure_ascii=False),
             ts or now, ts or now, ts or now, now, sender_id),
        )

    def rebuild_people_from_events(self):
        """从 events 全量重算 people 的计数与首末时间（幂等）。
        排除 is_self —— 大肥鱼自己不是"认识的人"。"""
        rows = self.db.execute(
            "SELECT sender_id, sender_name, COUNT(*) c, MIN(ts) a, MAX(ts) b, MAX(id) mid"
            " FROM events WHERE role='user' AND is_self=0 AND sender_id IS NOT NULL"
            " GROUP BY sender_id"
        ).fetchall()
        now = time.time()
        for r in rows:
            last_name = self.db.execute(
                "SELECT sender_name FROM events WHERE sender_id=? AND sender_name IS NOT NULL"
                " AND sender_name<>'' ORDER BY ts DESC LIMIT 1", (r["sender_id"],)
            ).fetchone()
            name = (last_name["sender_name"] if last_name else r["sender_name"]) or r["sender_id"]
            self.db.execute(
                "INSERT INTO people(sender_id,name,aliases,first_seen,last_seen,msg_count,updated_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(sender_id) DO UPDATE SET name=excluded.name,"
                " first_seen=excluded.first_seen, last_seen=excluded.last_seen,"
                " msg_count=excluded.msg_count, updated_at=excluded.updated_at",
                (r["sender_id"], name,
                 json.dumps([name], ensure_ascii=False), r["a"], r["b"], r["c"], now),
            )
        self.db.commit()
        return len(rows)

    def get_person(self, key):
        """按 sender_id 或名字/别名找人。"""
        row = self.db.execute("SELECT * FROM people WHERE sender_id=?", (key,)).fetchone()
        if row:
            return row
        for r in self.db.execute("SELECT * FROM people ORDER BY msg_count DESC"):
            names = [r["name"]] + json.loads(r["aliases"] or "[]")
            if key in [n for n in names if n]:
                return r
        # 模糊：包含关系
        for r in self.db.execute("SELECT * FROM people ORDER BY msg_count DESC"):
            names = [r["name"]] + json.loads(r["aliases"] or "[]")
            for n in names:
                if n and (key in n or n in key) and len(key) >= 2:
                    return r
        return None

    def add_fact(self, sender_id, fact, category="其他", confidence=0.8,
                 source="llm", source_ref=None, supersedes=None):
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO facts(sender_id,category,fact,confidence,source,source_ref,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (sender_id, category, fact, float(confidence), source, source_ref, now),
        )
        fid = cur.lastrowid
        for old in (supersedes or []):
            self.db.execute(
                "UPDATE facts SET superseded_by=?, superseded_at=? WHERE id=? AND superseded_by IS NULL",
                (fid, now, int(old)),
            )
        return fid

    def get_facts(self, sender_id, active_only=True, limit=60):
        sql = "SELECT * FROM facts WHERE sender_id=?"
        if active_only:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        return self.db.execute(sql, (sender_id, int(limit))).fetchall()

    def get_digest(self, day):
        return self.db.execute("SELECT * FROM digests WHERE day=?", (day,)).fetchone()

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass
