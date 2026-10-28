"""L0 归档器：把 livingmemory 的 conversations.db 增量搬到本插件自己的库。

为什么是"搬"而不是"自己监听消息"：
  · 不挂事件监听器 = 不参与事件分发 = 对回复延迟零影响，也不会有
    "某个 handler 把事件 stop 掉导致别人收不到"的连带风险
  · conversations.db 本来就已经在存全群消息（enable_full_group_capture=true）
  · **只读打开**（mode=ro），连锁都不会给 livingmemory 造成影响

为什么要搬：
  livingmemory 的 messages 表只保留最近 max_messages_per_session(1000) 条，
  且只删已总结的部分 —— 超过上限的老消息会被滑出。要"一直记住"，必须自己留一份。

去重与水位：
  · 不依赖 messages.id（livingmemory 会 DELETE，SQLite 的 rowid 会被复用，
    拿 id 当水位会漏消息）
  · 用 msg_key = sha1(session|ts|sender|content) 做 UNIQUE，重复同步自动 IGNORE
  · 水位只用来缩小扫描范围（ts > 上次水位 - 回看窗口），正确性靠 msg_key
"""
import os
import sqlite3

from .store import Store

# 每次回看多久（秒）。留大一点，防止上一轮同步时 livingmemory 正在写入、
# 或容器重启导致某段时间戳没扫到。重复的部分有 msg_key 兜底。
LOOKBACK = 3 * 24 * 3600


def _open_ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)


def conversations_path(data_dir: str) -> str:
    return os.path.join(data_dir, "plugin_data", "astrbot_plugin_livingmemory",
                        "conversations.db")


def sync(store: Store, data_dir: str, full: bool = False):
    """增量同步。返回 (新增条数, 扫描条数, 错误信息)。

    注意：本函数设计为在 asyncio.to_thread 的工作线程里跑，
    所以内部**自己开一份 sqlite 连接**，不和主线程共用。
    """
    src_path = conversations_path(data_dir)
    if not os.path.exists(src_path):
        return 0, 0, "找不到 conversations.db：%s" % src_path

    local = store.clone()  # 线程内独立连接
    try:
        return _sync_with(local, src_path, full)
    finally:
        local.close()


def _sync_with(store: Store, src_path: str, full: bool = False):
    last = 0.0 if full else float(store.get_meta("sync_ts", 0) or 0)
    since = max(0.0, last - LOOKBACK) if last else 0.0

    try:
        src = _open_ro(src_path)
    except Exception as e:
        return 0, 0, "打不开 conversations.db：%s" % e

    added = scanned = 0
    max_ts = last
    try:
        # 先建 sender_id → 最近正常名字 的映射（用于修补退化的 sender_name）
        name_map = {}
        for sid, name in src.execute(
            "SELECT sender_id, sender_name FROM messages"
            " WHERE sender_name IS NOT NULL AND sender_name<>''"
            "   AND sender_name<>sender_id AND length(sender_name)<=20"
            " ORDER BY timestamp ASC"
        ):
            if sid:
                name_map[sid] = name

        rows = src.execute(
            "SELECT session_id, role, content, sender_id, sender_name, group_id, timestamp"
            " FROM messages WHERE timestamp > ? ORDER BY timestamp ASC, id ASC",
            (since,),
        ).fetchall()
        scanned = len(rows)

        for session_id, role, content, sender_id, sender_name, group_id, ts in rows:
            if not content:
                continue
            ts = float(ts or 0)
            if ts > max_ts:
                max_ts = ts
            name = (sender_name or "").strip()
            # 退化判定：空 / 等于 id / 长得像 id（>20 字符）
            if not name or name == sender_id or len(name) > 20:
                name = name_map.get(sender_id) or name or sender_id
            if store.add_event(ts, session_id, group_id, sender_id, name, role, content):
                added += 1
        store.commit()
        if max_ts > last:
            store.set_meta("sync_ts", max_ts)
        # 每次同步后重新识别"哪些消息其实是大肥鱼自己发的"
        store.detect_self_ids()
    except Exception as e:
        return added, scanned, "同步出错：%s" % e
    finally:
        try:
            src.close()
        except Exception:
            pass

    return added, scanned, ""
