#!/usr/bin/env python3
"""把人格 12 的 system_prompt 替换为精简稿内容。

用法: python3 apply_persona.py <文本文件路径> [persona_id]

安全：改前整库备份到 data_v4.db.bak-persona-<时间戳>，并打印前后字符数。
"""
import json
import shutil
import sqlite3
import sys
from datetime import datetime

DB = "/AstrBot/data/data_v4.db"


def main():
    if len(sys.argv) < 2:
        print("用法: apply_persona.py <文件> [persona_id]")
        return 2
    path = sys.argv[1]
    pid = sys.argv[2] if len(sys.argv) > 2 else "12"

    text = open(path, encoding="utf-8").read().strip()
    if not text:
        print("!! 文件为空")
        return 2

    # 关键要素自检
    need = ["{{可爱}}", "{{哼}}", "《卫戍协议", "傲娇"]
    missing = [k for k in need if k not in text]
    if missing:
        print("!! 缺少关键要素，拒绝写入:", missing)
        return 2

    bak = "%s.bak-persona-%s" % (DB, datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy(DB, bak)
    print("已备份 ->", bak)

    c = sqlite3.connect(DB, timeout=15)
    old = c.execute("select system_prompt from personas where persona_id=?", (pid,)).fetchone()
    if not old:
        print("!! 找不到 persona", pid)
        return 2
    old_len = len(old[0] or "")

    c.execute("update personas set system_prompt=?, updated_at=datetime('now') where persona_id=?",
              (text, pid))
    c.commit()

    new = c.execute("select system_prompt, updated_at from personas where persona_id=?", (pid,)).fetchone()
    c.close()

    ok = (new[0] or "").strip() == text
    print("persona %s: %d -> %d 字符，写入校验: %s" % (pid, old_len, len(new[0] or ""), "通过" if ok else "失败"))
    print("updated_at:", new[1])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
