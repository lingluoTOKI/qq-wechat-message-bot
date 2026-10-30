"""style.db：规则/黑话/示例/审批日志。

只存提炼结果与 ≤120 字证据，不存聊天原文；所有 SQL 只出现在本文件。
"""
import sqlite3
import time


def record_zhipu_budget(response,origin,cost_path='/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db'):
    from datetime import datetime,timezone
    raw=response.raw_completion;u=raw.get('usage') or {}
    identity=raw.get('id');model=raw.get('model');inp=u.get('prompt_tokens');out=u.get('completion_tokens')
    if model!='glm-5.3-flashx' or not identity or ':GroupMessage:' not in origin or any(type(v) is not int or v<0 for v in [inp,out]):
        raise RuntimeError('智谱用量未知，停止后续调用')
    # Standard-price estimate, no assumption that account resource credits paid it.
    cost=(inp*2+out*7)/1e6
    c=sqlite3.connect(cost_path,timeout=.2)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM cost_supplements WHERE response_id=?',(identity,)).fetchone():return
            c.execute("""INSERT INTO cost_supplements(umo,provider_id,provider_model,response_id,
                token_input_other,token_input_cached,token_output,injection_total,cost_amount,currency_symbol,created_at)
                VALUES(?,?,?,?,?,0,?,0,?,'CNY',?)""",(origin,'zhipu/glm-5.3-flashx',model,identity,inp,out,cost,datetime.now(timezone.utc).isoformat()))
    finally:c.close()


def record_budget(response, origin, ledger_path='/AstrBot/data/plugin_data/astrbot_plugin_request_ledger/requests.db',
                  cost_path='/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db'):
    """Copy an observed request charge into group attribution, never invent usage."""
    from datetime import datetime, timezone
    identity = getattr(response,'id',None) or getattr(getattr(response,'raw_completion',None),'id',None)
    if not identity or ':GroupMessage:' not in origin:
        raise RuntimeError('学习请求缺少可核验的响应编号或群来源')
    ledger=sqlite3.connect('file:'+str(ledger_path)+'?mode=ro',uri=True,timeout=.2)
    try:
        row=ledger.execute('SELECT c.started,c.model,c.hit,c.miss,c.output,c.cost_nano FROM charges c JOIN attempts a ON a.id=c.attempt_id WHERE a.response_id=?',(identity,)).fetchone()
    finally:ledger.close()
    if not row or row[5] is None:raise RuntimeError('学习用量尚未可靠采集，停止后续调用')
    c=sqlite3.connect(cost_path,timeout=.2)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM cost_supplements WHERE response_id=?',(identity,)).fetchone():return
            c.execute('''INSERT INTO cost_supplements(umo,provider_id,provider_model,response_id,
                token_input_other,token_input_cached,token_output,injection_total,cost_amount,currency_symbol,created_at)
                VALUES(?,?,?,?,?,?,?,0,?,?,?)''',(origin,'deepseek/deepseek-flash',row[1],identity,row[3],row[2],row[4],row[5]/1e9,'CNY',datetime.fromtimestamp(row[0],timezone.utc).isoformat()))
    finally:c.close()

DDL = """
CREATE TABLE IF NOT EXISTS style_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id   TEXT NOT NULL,
  rule       TEXT NOT NULL,
  evidence   TEXT,
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  reviewed_at REAL,
  reviewed_by TEXT,
  used_count INTEGER DEFAULT 0,
  last_used_at REAL
);
CREATE INDEX IF NOT EXISTS idx_rules_group_status ON style_rules(group_id, status);

CREATE TABLE IF NOT EXISTS jargon (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL,
  term TEXT NOT NULL,
  meaning TEXT NOT NULL,
  confidence REAL DEFAULT 0.7,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  UNIQUE(group_id, term)
);

CREATE TABLE IF NOT EXISTS few_shots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL,
  user_text TEXT NOT NULL,
  bot_reply TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  actor TEXT,
  ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

TABLES = ("style_rules", "jargon", "few_shots")


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path, timeout=0.02)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)
        self.db.commit()

    # ---------- meta ----------
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (key, str(value)))
        self.db.commit()

    # ---------- 写入（只写 pending） ----------
    def add_rules(self, group_id: str, items: list, evidence_max: int = 120) -> int:
        """写入规则候选；同群同规则去重，超配额丢最旧 pending。"""
        added = 0
        now = time.time()
        for item in items:
            rule = str((item or {}).get("rule", "")).strip()
            if not rule:
                continue
            exists = self.db.execute(
                "SELECT 1 FROM style_rules WHERE group_id=? AND rule=?", (group_id, rule)
            ).fetchone()
            if exists:
                continue
            evidence = str((item or {}).get("evidence", "")).strip()[:evidence_max]
            self.db.execute(
                "INSERT INTO style_rules(group_id, rule, evidence, status, created_at)"
                " VALUES(?,?,?,'pending',?)",
                (group_id, rule, evidence, now),
            )
            added += 1
        self.db.commit()
        return added

    def add_jargon(self, group_id: str, items: list) -> int:
        added = 0
        now = time.time()
        for item in items:
            term = str((item or {}).get("term", "")).strip()
            meaning = str((item or {}).get("meaning", "")).strip()
            if not term or not meaning:
                continue
            try:
                conf = float((item or {}).get("confidence", 0.7))
            except (TypeError, ValueError):
                conf = 0.7
            cur = self.db.execute(
                "INSERT OR IGNORE INTO jargon(group_id, term, meaning, confidence, status, created_at)"
                " VALUES(?,?,?,?,'pending',?)",
                (group_id, term, meaning, max(0.0, min(1.0, conf)), now),
            )
            added += 1 if cur.rowcount else 0
        self.db.commit()
        return added

    def add_shots(self, group_id: str, items: list) -> int:
        added = 0
        now = time.time()
        for item in items:
            user_text = str((item or {}).get("user_text", "")).strip()
            bot_reply = str((item or {}).get("bot_reply", "")).strip()
            if not user_text or not bot_reply:
                continue
            dup = self.db.execute(
                "SELECT 1 FROM few_shots WHERE group_id=? AND user_text=?",
                (group_id, user_text[:200]),
            ).fetchone()
            if dup:
                continue
            self.db.execute(
                "INSERT INTO few_shots(group_id, user_text, bot_reply, status, created_at)"
                " VALUES(?,?,?,'pending',?)",
                (group_id, user_text[:200], bot_reply[:300], now),
            )
            added += 1
        self.db.commit()
        return added

    def enforce_caps(self, group_id: str, daily_cap: int, total_cap: int) -> int:
        """pending 超量时丢最旧的；approved 不受影响。返回删除条数。"""
        removed = 0
        pending = self.db.execute(
            "SELECT id FROM style_rules WHERE group_id=? AND status='pending'"
            " ORDER BY created_at DESC", (group_id,)
        ).fetchall()
        if len(pending) > daily_cap:
            for row in pending[daily_cap:]:
                self.db.execute("DELETE FROM style_rules WHERE id=?", (row["id"],))
                removed += 1
        total = self.db.execute(
            "SELECT COUNT(*) c FROM style_rules WHERE group_id=?", (group_id,)
        ).fetchone()["c"]
        if total > total_cap:
            extra = self.db.execute(
                "SELECT id FROM style_rules WHERE group_id=? AND status='pending'"
                " ORDER BY created_at ASC LIMIT ?", (group_id, total - total_cap)
            ).fetchall()
            for row in extra:
                self.db.execute("DELETE FROM style_rules WHERE id=?", (row["id"],))
                removed += 1
        self.db.commit()
        return removed

    # ---------- 查询 ----------
    def _rows(self, sql: str, args=()):
        return self.db.execute(sql, args).fetchall()

    def pending(self, table: str, group_id: str = "", limit: int = 20):
        if table not in TABLES:
            return []
        if group_id:
            return self._rows(
                "SELECT * FROM %s WHERE status='pending' AND group_id=?"
                " ORDER BY id DESC LIMIT ?" % table, (group_id, limit))
        return self._rows(
            "SELECT * FROM %s WHERE status='pending' ORDER BY id DESC LIMIT ?" % table, (limit,))

    def approved(self, table: str, group_id: str, limit: int = 10):
        if table not in TABLES:
            return []
        return self._rows(
            "SELECT * FROM %s WHERE status='approved' AND group_id=?"
            " ORDER BY id DESC LIMIT ?" % table, (group_id, limit))

    def counts(self, group_id: str = ""):
        out = {}
        for table in TABLES:
            if group_id:
                sql = "SELECT COUNT(*) c FROM %s WHERE status='pending' AND group_id=?" % table
                args = (group_id,)
            else:
                sql = "SELECT COUNT(*) c FROM %s WHERE status='pending'" % table
                args = ()
            out[table] = self.db.execute(sql, args).fetchone()["c"]
        return out

    def get_row(self, table: str, row_id: int):
        if table not in TABLES:
            return None
        return self.db.execute("SELECT * FROM %s WHERE id=?" % table, (row_id,)).fetchone()

    # ---------- 审批 ----------
    def review(self, table: str, row_id: int, action: str, actor: str = "") -> bool:
        """action: approve / reject。只能改 pending。"""
        if table not in TABLES or action not in ("approve", "reject"):
            return False
        status = "approved" if action == "approve" else "rejected"
        if table == "style_rules":
            cur = self.db.execute(
                "UPDATE style_rules SET status=?, reviewed_at=?, reviewed_by=?"
                " WHERE id=? AND status='pending'",
                (status, time.time(), actor, row_id),
            )
        else:
            cur = self.db.execute(
                "UPDATE %s SET status=? WHERE id=? AND status='pending'" % table,
                (status, row_id),
            )
        self.db.commit()
        if cur.rowcount:
            self.db.execute(
                "INSERT INTO review_log(target, target_id, action, actor, ts) VALUES(?,?,?,?,?)",
                (table, row_id, action, actor, time.time()),
            )
            self.db.commit()
            return True
        return False

    def cleanup_rejected(self, days: int = 30) -> int:
        cutoff = time.time() - days * 86400
        total = 0
        for table in TABLES:
            cur = self.db.execute(
                "DELETE FROM %s WHERE status='rejected' AND created_at<?" % table, (cutoff,))
            total += cur.rowcount
        self.db.commit()
        return total

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
