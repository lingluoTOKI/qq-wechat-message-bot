"""从服务器拉取关键配置，脱敏后生成可入库的配置快照。

用法：
    python scripts/ops/backup_remote_configs.py

输出到 configs/server/：
    *.snapshot.json  脱敏后的配置快照
    每个快照只保留结构与非敏感值，凭据/会话 ID 统一替换为占位符。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / ".robot-nopass.pem"
HOST = "101.132.104.41"
OUT = ROOT / "configs" / "server"

DATA_ROOT = "/opt/bot-stack/astrbot/data"
FILES = {
    "cost_control": f"{DATA_ROOT}/plugin_data/astrbot_plugin_cost_control/config.json",
    "proactive_chat": f"{DATA_ROOT}/config/astrbot_plugin_proactive_chat_config.json",
    "livingmemory": f"{DATA_ROOT}/config/astrbot_plugin_livingmemory_config.json",
    "biliread": f"{DATA_ROOT}/config/astrbot_plugin_biliread_config.json",
    "bilibili": f"{DATA_ROOT}/config/astrbot_plugin_bilibili_config.json",
    # 颜文字库是数据文件，但改动后需可追溯，一并快照
    "kaomoji": f"{DATA_ROOT}/plugin_data/astrbot_plugin_kaomoji/kaomoji.json",
}

# 键名直接视为凭据
SECRET_KEY = re.compile(
    r"(?i)(token|secret|password|api_key|apikey|cookie|sessdata|jwt|access_key|secret_key|webhook)"
)
# 键名直接视为会话/身份标识
ID_KEY = re.compile(
    r"(?i)^(session_id|group_id|user_id|umo|qq|allowed_ids|identity_aliases|"
    r"isolated_sessions|admins_id|target_id|target_user|open_id|union_id)$"
)
# 值形如 "大肥鱼-官方:FriendMessage:xxxx" 或 "GroupMessage:xxxx"
ID_VALUE = re.compile(r"(?:FriendMessage|GroupMessage|TempMessage|UMO)|[0-9A-F]{32}")

PLACEHOLDER_ID = "<已脱敏-会话ID>"
PLACEHOLDER_SECRET = "<已脱敏-凭据>"


def sanitize(node):
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if SECRET_KEY.search(str(key)):
                result[key] = ""
            elif ID_KEY.match(str(key)) and isinstance(value, (str, int)):
                result[key] = PLACEHOLDER_ID
            else:
                result[key] = sanitize(value)
        return result
    if isinstance(node, list):
        return [sanitize(item) for item in node]
    if isinstance(node, str) and ID_VALUE.search(node):
        return PLACEHOLDER_ID
    return node


def read_remote(client: paramiko.SSHClient, path: str) -> dict:
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8-sig"))
    finally:
        sftp.close()


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", key_filename=str(KEY), timeout=15)

    OUT.mkdir(parents=True, exist_ok=True)
    for name, path in FILES.items():
        data = sanitize(read_remote(client, path))
        (OUT / f"{name}.snapshot.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"写入 configs/server/{name}.snapshot.json")

    # cmd_config 体积大且含 provider 凭据，只保留本次调整的上下文压缩段
    cmd_config = read_remote(client, f"{DATA_ROOT}/cmd_config.json")
    compression = cmd_config["agent_runner"]["config"].get("compression", {})
    (OUT / "agent_runner.compression.snapshot.json").write_text(
        json.dumps({"compression": compression}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("写入 configs/server/agent_runner.compression.snapshot.json")

    client.close()


if __name__ == "__main__":
    main()
