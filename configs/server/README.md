# 服务器配置快照

从生产服务器（`101.132.104.41`，容器 `astrbot`，数据目录 `/opt/bot-stack/astrbot/data`）拉取的配置快照，用于追溯与重建，**不能直接覆盖回服务器**。

生成方式：

```bash
python scripts/ops/backup_remote_configs.py
```

## 文件说明

| 文件 | 源路径 | 说明 |
|---|---|---|
| `cost_control.snapshot.json` | `plugin_data/astrbot_plugin_cost_control/config.json` | 预算上限（月 20 / 日 5 / 会话日 3 元）、定价、缓存诊断开关 |
| `proactive_chat.snapshot.json` | `config/astrbot_plugin_proactive_chat_config.json` | 主动消息免打扰与间隔（私聊 90–360 分钟、群 30–120 分钟、quiet 1–8） |
| `livingmemory.snapshot.json` | `config/astrbot_plugin_livingmemory_config.json` | 记忆召回与备份保留策略 |
| `biliread.snapshot.json` | `config/astrbot_plugin_biliread_config.json` | B 站读取参数（字幕/评论/视觉） |
| `bilibili.snapshot.json` | `config/astrbot_plugin_bilibili_config.json` | 订阅推送参数、`llm_tools_enabled` 开关 |
| `agent_runner.compression.snapshot.json` | `cmd_config.json` 的 `agent_runner.config.compression` | 上下文压缩参数（`max_turns 40`、`keep_recent_ratio 0.3`） |
| `kaomoji.snapshot.json` | `plugin_data/astrbot_plugin_kaomoji/kaomoji.json` | 颜文字库；已把 `。~…` 换成视觉近似的 `｡～⋯`，避免分段回复在颜文字内部断句 |

## 脱敏规则

- 键名含 `token / secret / password / api_key / cookie / sessdata / jwt / webhook` 的值一律清空。
- 会话 ID、群号、QQ 号统一替换为 `<已脱敏-会话ID>`。
- `cmd_config.json` 只取上下文压缩段，不含 provider 凭据、dashboard JWT 密钥与人格文本。

恢复时需手工补回：B 站 `sessdata`、`vision_api_key`、主动消息 `web_admin.password`，以及 `cmd_config.json` 中其余配置（本快照不含）。
