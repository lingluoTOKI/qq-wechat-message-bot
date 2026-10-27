"""往 compose 的 astrbot.environment 里加阿里云 pip 镜像（加速启动时的依赖安装）"""
import shutil
from datetime import datetime

P = "/opt/bot-stack/docker-compose.yml"
shutil.copy(P, P + ".bak-pipmirror-" + datetime.now().strftime("%Y%m%d-%H%M"))

s = open(P, encoding="utf-8").read()

if "PIP_INDEX_URL" in s:
    print("已存在 PIP_INDEX_URL，跳过")
    raise SystemExit(0)

OLD = "      - PYTHONPATH=/AstrBot/data/pylibs\n"
NEW = ("      - PYTHONPATH=/AstrBot/data/pylibs\n"
       "      - PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/\n"
       "      - PIP_TRUSTED_HOST=mirrors.aliyun.com\n")

if OLD not in s:
    print("未找到 PYTHONPATH 行，未修改")
    raise SystemExit(1)

s = s.replace(OLD, NEW, 1)
open(P, "w", encoding="utf-8").write(s)
print("已加入 PIP_INDEX_URL")
