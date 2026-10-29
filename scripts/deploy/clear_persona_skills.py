#!/usr/bin/env python3
"""清空所有人格的 skills 白名单，彻底停掉 Skills 块注入。

原理（astr_main_agent.py:628）：
    if persona and persona.get("skills") is not None:
        if not persona["skills"]:
            skills = []          # ← 显式空列表 → 后续 if skills: 不成立 → 不注入
所以把 skills 设成 JSON "[]" 就能完全跳过整块（4 个技能 + 7 条规则）。

依据：框架自己在该块末尾写着「User has not enabled the Computer Use
feature. You cannot use shell or Python to perform skills.」——技能根本无法执行，
这 ~700 token 是纯粹浪费。

幂等：重复执行无副作用。已从 data_v4.db 就地备份到 personas.bak-skills-<时间>。
"""
import json
import shutil
import sqlite3
from datetime import datetime

DB = "/AstrBot/data/data_v4.db"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def main():
    # 文件级备份（容器内 /AstrBot/data 是持久卷，备份会保留）
    bak = "%s.bak-skills-%s" % (DB, STAMP)
    shutil.copy(DB, bak)
    print("已备份 DB ->", bak)

    # 用读写模式连接（需要写；WAL 模式要允许）
    c = sqlite3.connect(DB, timeout=15)
    rows = list(c.execute("select persona_id, skills, tools from personas"))
    print("\n当前状态:")
    for pid, sk, tl in rows:
        print("  %-10s skills=%-16s tools=%s" % (pid, str(sk)[:16],
                                                 "白名单" if tl and tl != "null" and json.loads(tl) else "NULL(全量)"))

    # 注意：这一列存的是 JSON 文本，未设置时是字符串 "null"（不是 SQL NULL）。
    # 两种形态都要处理，否则 update 会静默影响 0 行。
    c.execute("update personas set skills = '[]' "
              "where skills is null or trim(skills) = '' or trim(skills) = 'null'")
    changed = c.total_changes
    c.commit()
    print("\n影响行数: %d" % changed)

    print("\n改后状态:")
    for pid, sk, tl in c.execute("select persona_id, skills, tools from personas"):
        n_sk = "[] 空(不注入)" if json.loads(sk) == [] else str(sk)
        tl_v = json.loads(tl) if tl else None
        print("  %-10s skills=%-14s tools=%s" % (pid, n_sk,
                                                 ("%d 个白名单" % len(tl_v)) if tl_v else "NULL(全量)"))
    c.close()

    # 校验：所有人格 skills 必须都是字面量 []（不是 "null" 文本）
    c = sqlite3.connect(DB, timeout=10)
    bad = []
    for pid, sk in c.execute("select persona_id, skills from personas"):
        try:
            if json.loads(sk) != []:
                bad.append((pid, sk))
        except Exception:
            bad.append((pid, sk))
    c.close()
    if bad:
        print("\n!! 以下人格 skills 仍非 []:", bad)
        return 1
    print("\n校验通过：所有人格 skills 均为 []，Skills 块将不再注入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
