#!/usr/bin/env python3
"""把 4 个用不到的内置技能移出 skills 根目录，彻底停止 Skills 块注入。

为什么必须在目录层做，而不是只改人格表：
- 只改 persona.skills=[] 只能覆盖「从 DB 读完整人格」的路径；
- 另一条路径（从 cmd_config 读 persona，缺少 skills 字段）会退化成
  「注入全部内置技能」，人格表改不到它。
- 移走目录后，list_skills() 返回空列表，任何路径都注入不了。

安全性：
- 技能执行本来就不可用（框架自述「User has not enabled the Computer Use
  feature. You cannot use skills.」），移走不影响任何现有功能。
- 目录是移动到持久卷下的 _disabled_skills，随时可移回。
"""
import os
import shutil

SRC = "/AstrBot/astrbot/builtin_stars/astrbot/skills"
DST = "/AstrBot/data/_disabled_skills"


def main():
    if not os.path.isdir(SRC):
        print("!! 源目录不存在:", SRC)
        return 1
    os.makedirs(DST, exist_ok=True)

    moved = []
    for name in sorted(os.listdir(SRC)):
        p = os.path.join(SRC, name)
        if not os.path.isdir(p):
            continue
        tgt = os.path.join(DST, name)
        if os.path.exists(tgt):
            shutil.rmtree(tgt)
        shutil.move(p, tgt)
        moved.append(name)

    print("已移出 %d 个技能目录 -> %s" % (len(moved), DST))
    for n in moved:
        print("   -", n)
    print("剩余在 skills 根目录的条目:", os.listdir(SRC) or "(空)")

    # 立即验证框架是否还看得见技能（无需重启，SkillManager 每请求新建）
    try:
        import sys
        sys.path.insert(0, "/AstrBot")
        from astrbot.core.skills import SkillManager

        sm = SkillManager()
        for rt in ("none", "local"):
            try:
                s = sm.list_skills(active_only=True, runtime=rt)
                print("list_skills(runtime=%-5s) -> %d 个 %s"
                      % (rt, len(s), [x.name for x in s]))
            except Exception as e:
                print("list_skills(runtime=%s) 异常: %s" % (rt, e))
    except Exception as e:
        print("验证导入失败:", type(e).__name__, e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
