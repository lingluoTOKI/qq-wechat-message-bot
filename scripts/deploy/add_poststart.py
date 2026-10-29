"""在 compose 的 astrbot 服务里加 post_start 自动打补丁"""
import re
import shutil
from datetime import datetime

P = "/opt/bot-stack/docker-compose.yml"
shutil.copy(P, P + ".bak-poststart-" + datetime.now().strftime("%Y%m%d-%H%M"))

s = open(P, encoding="utf-8").read()

if "post_start" in s:
    print("已存在 post_start，跳过")
    raise SystemExit(0)

BLOCK = """    post_start:
      - command: python3 /AstrBot/data/patch/patch_astrbot_stats.py
        user: root
"""

# 在 astrbot 服务的 logging 块之后插入（即 snowluma 之前）
idx = s.find("\n  snowluma:")
if idx == -1:
    # 回退：找第二个服务的起始
    m = re.search(r"\n  [a-zA-Z_]+:\n", s[s.find("astrbot:"):])
    idx = s.find("astrbot:") + m.start() if m else -1
if idx == -1:
    print("未找到插入点，未修改")
    raise SystemExit(1)

s = s[:idx] + "\n" + BLOCK + s[idx:]
open(P, "w", encoding="utf-8").write(s)
print("已插入 post_start")
