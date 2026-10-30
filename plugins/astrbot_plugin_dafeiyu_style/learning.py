"""学习流水线：只读 events → 两段式提炼 → 写 pending。

本模块不依赖 astrbot，可单独跑单元测试；模型调用由调用方以 ask 注入。
"""
import json
import asyncio
import os
import sqlite3
import urllib.parse
from pathlib import Path

try:  # 兼容 AstrBot 包内加载与本机单元测试
    from . import prompt
    from . import quality
except ImportError:  # pragma: no cover
    import prompt
    import quality


def _ro_uri(path: str) -> str:
    return "file:%s?mode=ro" % urllib.parse.quote(Path(path).as_posix())


def read_events(db_path: str, since_id: int, groups, min_len: int, max_len: int, limit: int):
    """读取增量消息。库不存在/表不存在/被锁一律返回空，绝不抛异常。"""
    if not db_path or not os.path.exists(db_path):
        return [], int(since_id)
    conn = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        group_sql = ''
        args = [int(since_id), int(min_len), int(max_len)]
        if groups:
            allowed = sorted({str(g) for g in groups})
            # Runtime/budget identity is the full session origin. Archive group_id
            # may be a platform-specific label; do not infer one from the other.
            canonical = all(':GroupMessage:' in g for g in allowed)
            column = 'session_id' if canonical else 'group_id'
            group_sql = ' AND ' + column + ' IN (' + ','.join('?' for _ in allowed) + ')'
            args.extend(allowed)
        args.append(int(limit))
        rows = conn.execute(
            "SELECT id, session_id, group_id, sender_id, sender_name, content, ts"
            " FROM events WHERE id > ? AND is_self=0"
            " AND group_id IS NOT NULL AND group_id != ''"
            " AND LENGTH(content) BETWEEN ? AND ?"
            + group_sql + " ORDER BY id ASC LIMIT ?",
            args,
        ).fetchall()
        out = [dict(r) for r in rows]
        max_id = max([int(since_id)] + [int(r["id"]) for r in out])
        return out, max_id
    except Exception:
        return [], int(since_id)
    finally:
        if conn is not None:
            conn.close()


def read_groups(db_path: str, limit: int = 50):
    """配置未指定群时，用归档里出现过的群作为学习范围。"""
    if not db_path or not os.path.exists(db_path):
        return []
    conn = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=5)
        rows = conn.execute(
            "SELECT session_id FROM events WHERE group_id IS NOT NULL"
            " AND group_id != '' AND instr(session_id, ':GroupMessage:')>0"
            " GROUP BY session_id ORDER BY MAX(id) DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def _json_loads(text: str):
    """去掉代码块包裹并截取最外层对象；失败返回 None。"""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None


def parse_payload(text: str):
    """把模型输出解析成受控结构；任何缺失/类型错误都按空处理。"""
    obj = _json_loads(text)
    if not isinstance(obj, dict):
        return None

    def _list(key):
        value = obj.get(key)
        return value if isinstance(value, list) else []

    rules = []
    for item in _list("rules"):
        if isinstance(item, dict) and str(item.get("rule", "")).strip():
            rules.append({"rule": str(item["rule"]).strip()[:80],
                          "evidence": str(item.get("evidence", "")).strip()[:120]})
    jargon = []
    for item in _list("jargon"):
        if isinstance(item, dict) and str(item.get("term", "")).strip() \
                and str(item.get("meaning", "")).strip():
            jargon.append({"term": str(item["term"]).strip()[:30],
                           "meaning": str(item["meaning"]).strip()[:60],
                           "confidence": item.get("confidence", 0.7)})
    shots = []
    for item in _list("few_shots"):
        if isinstance(item, dict) and str(item.get("user_text", "")).strip() \
                and str(item.get("bot_reply", "")).strip():
            shots.append({"user_text": str(item["user_text"]).strip()[:200],
                          "bot_reply": str(item["bot_reply"]).strip()[:300]})
    return {"rules": rules[:6], "jargon": jargon[:5], "few_shots": shots[:3]}


def parse_keep(text: str):
    """粗筛结果；解析失败返回 None（调用方按「全保留」处理）。"""
    obj = _json_loads(text)
    if not isinstance(obj, dict):
        return None
    keep = obj.get("keep")
    if not isinstance(keep, list):
        return None
    out = set()
    for item in keep:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


async def review_existing(db_path,group_id,store):
    rows,_=await asyncio.to_thread(read_events,db_path,0,[group_id],1,10000,500)
    counts={'approved':0,'rejected':0,'pending':0}
    for table in ('style_rules','jargon','few_shots'):
        for item in store.pending(table,group_id,100):
            action,why=quality.decision(table,item,rows)
            if action and store.review(table,item['id'],action,'auto-local: '+why):
                counts['approved' if action=='approve' else 'rejected']+=1
            else:counts['pending']+=1
    return counts


async def process_group(*, db_path, group_id, since_id, cfg, ask, store):
    """处理一个群的一批增量消息。

    ask(system, user, stage) 由调用方提供；预算类异常直接向上抛，游标不推进。
    """
    rows, max_id = await asyncio.to_thread(read_events,
        db_path, since_id, [group_id],
        int(cfg.get("min_msg_len", 6)), int(cfg.get("max_msg_len", 300)),
        min(40, int(cfg.get("max_batch", 40))),
    )
    if len(rows) < int(cfg.get("min_messages", 30)):
        return {"advanced": False, "max_id": max_id, "added": {},
                "error": "too_few", "kept": 0}

    rows = [r for r in rows if not quality.exclusion(r['content'])]
    if not rows:
        return {"advanced": True, "max_id": max_id, "added": {},
                "error": "filtered_debug", "kept": 0}

    keep = parse_keep(await ask(prompt.SYSTEM, prompt.coarse_prompt(rows), "coarse"))
    if keep is not None:
        picked = [r for r in rows if int(r["id"]) in keep]
        if not picked:
            # 本轮没有值得学的：推进游标，避免同一批反复烧钱
            return {"advanced": True, "max_id": max_id, "added": {},
                    "error": "nothing_to_learn", "kept": 0}
    else:
        picked = rows

    payload = parse_payload(await ask(prompt.SYSTEM, prompt.refine_prompt(picked), "refine"))
    if payload is None:
        return {"advanced": False, "max_id": max_id, "added": {},
                "error": "parse", "kept": len(picked)}

    # Evidence must be a literal excerpt; examples must not persist whole messages.
    originals = [str(r['content']) for r in picked]
    payload['rules'] = [r for r in payload['rules'] if r.get('evidence')
        and any(r['evidence'] in text for text in originals)]
    payload['few_shots'] = [r for r in payload['few_shots']
        if not any(r['user_text'] == text or r['bot_reply'] == text for text in originals)]

    added = {
        "rules": store.add_rules(group_id, payload["rules"],
                                 int(cfg.get("evidence_max_chars", 120))),
        "jargon": store.add_jargon(group_id, payload["jargon"]),
        "few_shots": store.add_shots(group_id, payload["few_shots"]),
    }
    store.enforce_caps(group_id, int(cfg.get("daily_rule_cap", 8)),
                       int(cfg.get("total_rule_cap", 60)))
    if cfg.get('auto_review', True):
        for table in ('style_rules','jargon','few_shots'):
            for item in store.pending(table,group_id,100):
                action,why=quality.decision(table,item,rows)
                if action:
                    store.review(table,item['id'],action,'auto-local: '+why)
    return {"advanced": True, "max_id": max_id, "added": added,
            "error": None, "kept": len(picked)}
