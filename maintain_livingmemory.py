#!/usr/bin/env python3
"""LivingMemory 每周维护
1) 合并 bot 分身节点（私聊 bot:xxx / 哈希 / qq_official / unknown_selfid -> 主节点）
2) 重算 mentioned_in 边权重（按人名在 fact 文本中的位置分级）
幂等：重复执行不产生额外变化。
"""
import glob
import json
import os
import re
import shutil
import sqlite3
import time

LM = "/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db"
KEEP_ID = 260                      # 大肥鱼-小洛
SKIP_IDS = {1031}                  # 定时任务（cron 虚拟身份，保留）
# 只认精确的 bot 自身标识；不要用「32位哈希」做判定——群里每个真实用户的 sender_id 都是哈希
SELF_KEYS = {
    "qq_official:bot:大肥鱼-官方",
    "qq_official:qq_official",
    "qq_official:unknown_selfid",
}
SELF_SENDER_IDS = {"bot:大肥鱼-官方"}
SELF_VALUES = {"qq_official", "unknown_selfid"}

EXTRA = {
    "泠泺TOKI": ["TOKI", "老大", "泠泺", "老大(泠泺TOKI)"],
    "大肥鱼-小洛": ["小洛", "大肥鱼", "鱼"],
    "后排靠窗 王の故乡": ["后排靠窗", "王の故乡", "故乡"],
    "千本の日光.": ["千本の日光", "千本"],
}
NOISE = {"的", "了", "我", "他", "她"}

NOW = time.strftime("%Y%m%d-%H%M%S")


def backup():
    """用 sqlite3 官方 backup API 做一致性快照（直接 cp 运行中的 WAL 库可能得到损坏副本）"""
    bak = LM + ".bak-maint-%s" % NOW
    src = sqlite3.connect(LM, timeout=30)
    dst = sqlite3.connect(bak)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(glob.glob(LM + ".bak-maint-*"))[:-4]:
        try:
            os.remove(old)
        except Exception:
            pass
    return bak


def keys_for(name):
    out = {name}
    n = name.replace(" ", "").replace(".", "").replace("（", "").replace("）", "")
    out.add(n)
    if " " in name:
        out.add(name.split(" ")[0])
    if len(n) >= 4:
        out.add(n[:4])
    for a in EXTRA.get(name, []):
        out.add(a)
    return {k for k in out if k and len(k) >= 2 and k not in NOISE}


def cjk_bigrams(text):
    out, run = [], []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            run.append(ch)
        else:
            if len(run) >= 2:
                out += [run[i] + run[i + 1] for i in range(len(run) - 1)]
            elif run:
                out.append(run[0])
            run = []
    if len(run) >= 2:
        out += [run[i] + run[i + 1] for i in range(len(run) - 1)]
    elif run:
        out.append(run[0])
    return out


def merge_duplicates(c):
    persons = c.execute("SELECT id, node_value, metadata FROM graph_nodes WHERE node_type='person'").fetchall()
    keep = next((p for p in persons if p[0] == KEEP_ID), None)
    if not keep:
        return 0, 0
    dupes = []
    for pid, val, meta in persons:
        if pid in SKIP_IDS or pid == KEEP_ID:
            continue
        try:
            m = json.loads(meta or "{}")
        except Exception:
            m = {}
        ikey = str(m.get("identity_key") or "")
        sid = str(m.get("sender_id") or "")
        # 严格判定：只合并 bot 自己的分身，绝不碰真实群友
        if ikey in SELF_KEYS or sid in SELF_SENDER_IDS or ikey.endswith("bot:大肥鱼-官方"):
            dupes.append(pid)
        elif (val or "") in SELF_VALUES and (ikey.startswith("qq_official:qq_official")
                                             or ikey.startswith("qq_official:unknown_selfid")):
            dupes.append(pid)

    moved = merged = 0
    if len(dupes) > 3:
        names = [c.execute("SELECT node_value FROM graph_nodes WHERE id=?", (d,)).fetchone()[0] for d in dupes]
        print("[安全阀] 待合并节点 %d 个，超过阈值 3，已跳过合并。清单：%s" % (len(dupes), names[:10]))
        return 0, 0
    for did in dupes:
        edges = c.execute("SELECT id, source_node_id, target_node_id, relation_type, weight FROM graph_edges "
                          "WHERE source_node_id=? OR target_node_id=?", (did, did)).fetchall()
        for eid, src, tgt, rel, w in edges:
            ns = KEEP_ID if src == did else src
            nt = KEEP_ID if tgt == did else tgt
            if ns == nt:
                c.execute("DELETE FROM graph_edges WHERE id=?", (eid,))
                continue
            dup = c.execute("SELECT id, weight FROM graph_edges WHERE source_node_id=? AND target_node_id=? "
                            "AND relation_type=? AND id!=?", (ns, nt, rel, eid)).fetchone()
            if dup:
                c.execute("UPDATE graph_edges SET weight=? WHERE id=?", (max(w, dup[1]), dup[0]))
                c.execute("DELETE FROM graph_edges WHERE id=?", (eid,))
                merged += 1
            else:
                c.execute("UPDATE graph_edges SET source_node_id=?, target_node_id=? WHERE id=?", (ns, nt, eid))
                moved += 1
        for (ent,) in c.execute("SELECT entry_id FROM graph_entry_nodes WHERE node_id=?", (did,)).fetchall():
            c.execute("INSERT OR IGNORE INTO graph_entry_nodes(entry_id, node_id) VALUES(?,?)", (ent, KEEP_ID))
        c.execute("DELETE FROM graph_entry_nodes WHERE node_id=?", (did,))
        c.execute("DELETE FROM graph_nodes WHERE id=?", (did,))
    return len(dupes), moved + merged


def reweight(c):
    persons = c.execute("SELECT id, node_value FROM graph_nodes WHERE node_type='person'").fetchall()
    pname = dict(persons)
    alias = {pid: sorted(keys_for(val), key=len, reverse=True) for pid, val in persons}
    edges = c.execute("SELECT e.id, e.source_node_id, t.node_value FROM graph_edges e "
                      "JOIN graph_nodes t ON t.id=e.target_node_id WHERE e.relation_type='mentioned_in'").fetchall()
    changed = 0
    for eid, src, text in edges:
        text = text or ""
        keys = alias.get(src, [])
        L = max(len(text), 1)
        pos = -1
        for k in keys:
            i = text.find(k)
            if i >= 0 and (pos < 0 or i < pos):
                pos = i
        n_people = sum(1 for _, ks in alias.items() if any(text.find(k) >= 0 for k in ks))
        is_bot = pname.get(src) == "大肥鱼-小洛"
        if pos < 0:
            w = 0.85 if (is_bot and text.startswith("我")) else 0.20
        elif pos <= 2 and n_people == 1:
            w = 0.90
        elif n_people >= 3:
            w = 0.30
        elif pos / L < 0.35:
            w = 0.70
        elif pos / L >= 0.6:
            w = 0.30
        else:
            w = 0.50
        row = c.execute("SELECT weight FROM graph_edges WHERE id=?", (eid,)).fetchone()
        if not row:
            continue
        cur = row[0]
        if abs(cur - w) > 1e-6:
            c.execute("UPDATE graph_edges SET weight=?, updated_at=? WHERE id=?",
                      (w, time.strftime("%Y-%m-%dT%H:%M:%S+00:00"), eid))
            changed += 1
    return len(edges), changed


def main():
    print("=" * 50)
    print("[维护开始]", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("[备份]", backup())
    c = sqlite3.connect(LM, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    try:
        person_before = c.execute("SELECT COUNT(*) FROM graph_nodes WHERE node_type='person'").fetchone()[0]
        nodes_before = c.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edges_before = c.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

        n_dupe, n_edge = merge_duplicates(c)
        c.commit()
        print("[分身合并] 合并节点 %d 个，处理边 %d 条" % (n_dupe, n_edge))

        n_scan, n_changed = reweight(c)
        c.commit()
        print("[边权重重算] 扫描 %d 条，调整 %d 条" % (n_scan, n_changed))

        person_after = c.execute("SELECT COUNT(*) FROM graph_nodes WHERE node_type='person'").fetchone()[0]
        nodes_after = c.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edges_after = c.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        dangling = c.execute("""SELECT COUNT(*) FROM graph_edges e
            WHERE NOT EXISTS (SELECT 1 FROM graph_nodes n WHERE n.id=e.source_node_id)
               OR NOT EXISTS (SELECT 1 FROM graph_nodes n WHERE n.id=e.target_node_id)""").fetchone()[0]
        print("[结果] person %d->%d | 节点 %d->%d | 边 %d->%d | 悬空 %d"
              % (person_before, person_after, nodes_before, nodes_after, edges_before, edges_after, dangling))
        if n_dupe or n_changed:
            print("[提示] 有改动，建议 reload 插件：python3 /tmp/reload_one.py astrbot_plugin_livingmemory")
    finally:
        c.close()
    print("[维护结束]")


if __name__ == "__main__":
    main()
