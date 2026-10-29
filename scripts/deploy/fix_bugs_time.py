"""把 bugs.json 里修复前按 UTC 记录的时间统一 +8 小时改为北京时间。

只处理缺少 reported_at 时间戳的旧记录（即时区修复之前写入的）。
采用原子写（临时文件 + os.replace），避免与运行中的插件读写冲突。
"""
import json
import os
import shutil
from datetime import datetime, timedelta

PATH = os.environ.get("BUGS_PATH", "/opt/bot-stack/astrbot/data/plugin_data/wushu_ops/bugs.json")
SHIFT = timedelta(hours=8)

if not os.path.exists(PATH):
    print("未找到 bugs.json，跳过")
    raise SystemExit(0)

backup = PATH + ".bak-timefix-" + datetime.now().strftime("%Y%m%d-%H%M")
shutil.copy2(PATH, backup)
print("已备份 ->", backup)

with open(PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

changed = 0
for bug in data.get("bugs", []):
    # 已有时间戳的新记录由插件按北京时间写入，不动
    if bug.get("reported_at") or bug.get("last_report_at"):
        continue
    raw = bug.get("time")
    if not raw:
        continue
    try:
        dt = datetime.strptime("2026-" + str(raw)[:11], "%Y-%m-%d %H:%M")
    except ValueError:
        print("  跳过无法解析: %r" % raw)
        continue
    new = dt + SHIFT
    new_raw = new.strftime("%m-%d %H:%M")
    if new_raw != raw:
        print("  #%s  %s -> %s" % (bug.get("id"), raw, new_raw))
        bug["time"] = new_raw
        changed += 1

if changed:
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
print("共修正 %s 条" % changed)
