import time, shutil, py_compile

P = "/AstrBot/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py"
OLD = 'abm.self_id = bot_mention_ids[0] if bot_mention_ids else "qq_official"'
NEW = 'abm.self_id = "qq_official"  # 固定self_id：避免被@/未@时id跳变破坏前缀缓存（真实mention id仅用于剥离@标记）'

bak = P + ".bak-selfid-" + time.strftime("%Y%m%d-%H%M%S")
s = open(P, encoding="utf-8").read()
cnt = s.count(OLD)
if cnt != 1:
    print("ABORT: occurrences=%d (expect 1)" % cnt)
    raise SystemExit(1)
shutil.copy2(P, bak)
s = s.replace(OLD, NEW)
open(P, "w", encoding="utf-8").write(s)
py_compile.compile(P, doraise=True)
print("PATCHED OK; backup=%s" % bak)
print("NEW line(s):")
for ln in s.splitlines():
    if 'abm.self_id = "qq_official"' in ln:
        print("  " + ln.strip())
