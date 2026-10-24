# qq-wechat-message-bot

个人 QQ / 微信重要消息筛选与摘要机器人。监听群聊和私聊，用大模型自动提取待办、重要通知和参考资料，紧急事项即时私聊推送，其余每天汇总一次。

## 功能

- **多平台监听**：QQ（通过 SnowLuma / OneBot WebSocket）+ 微信（通过 wechatauto 数据库轮询）
- **智能事项提取**：调用智谱 GLM 模型，从群聊消息中提取独立事项，区分 `urgent / important / useful / reference` 四级
- **即时推送**：命中紧急关键词（@本人、截止、考试、报名等）的消息立即私聊提醒
- **每日摘要**：每天 21:00 汇总当日重要事项，分 QQ / 微信两条推送
- **参考资料库**：有价值但不必推送的消息自动归档到 Web 面板，随时翻阅
- **Token 预算监控**：实时统计 API 消耗，余额不足自动暂停分析并通知
- **Web 管理面板**：查看今日待办、参考资料、关注群聊、token 用量，De Stijl 风格界面

## 工作原理

```
QQ消息 ──► SnowLuma (OneBot WS) ──┐
                                   ├──► events 队列 ──► 攒批(2min) ──► GLM 批量分析 ──► 待办/参考资料
微信消息 ──► wechat-listener ──────┘                          │
                                                             ├─ urgent → 私聊推送
                                                             ├─ important/useful → 今日待办
                                                             └─ reference → 参考资料库
```

核心设计：
- **批量分析**：每 120 秒攒一轮，一次请求最多带 6 个会话，摊薄 system prompt 固定成本
- **指纹缓存**：消息内容哈希不变则直接复用上次分析结果，不重复调 API
- **增量更新**：已有历史分析的会话只分析新增消息，避免全量重算
- **噪声过滤**：表情、纯广告、无意义刷屏在调模型前先本地规则丢弃

## 快速开始

### 依赖

- Python 3.11+
- SnowLuma（QQ 协议端，提供 OneBot WebSocket）
- 微信 PC 端（wechatauto 读取本地消息数据库）
- 智谱 BigModel API Key

### 安装

```bash
pip install -r 个人QQ重要消息提醒/requirements.txt
```

### 配置

复制配置模板并填写：

```bash
cp 个人QQ重要消息提醒/config.example.json 个人QQ重要消息提醒/config.json
```

关键配置项：

| 字段 | 说明 |
|---|---|
| `qq_ws` | SnowLuma 的 OneBot WebSocket 地址 |
| `target_user` | 接收推送的 QQ 号 |
| `model` / `api_base` | 智谱模型名和 API 地址 |
| `profile` | 你的身份信息（学校、班级、考研目标等），模型据此判断消息与你是否相关 |
| `batch_seconds` | 攒批间隔，默认 120 秒 |
| `quiet_seconds` | 群聊安静多久后入批，默认 120 秒 |

API Key 首次启动时通过引导流程安全存储（DPAPI 加密），不落明文。

### 启动

```powershell
# 启动 BOT 主进程
powershell -File 个人QQ重要消息提醒/start.ps1

# 启动 Web 管理面板（另开终端）
powershell -File 管理面板/启动管理面板.bat
```

面板默认运行在 `http://127.0.0.1:8765`。

## 目录结构

```
├── 个人QQ重要消息提醒/
│   ├── relay.py              # 主进程：消息监听、LLM分析、推送
│   ├── wechat_listener.py    # 微信消息轮询
│   ├── workflow_plus.py      # 待办工作流
│   ├── workbench_store.py    # 面板数据存储
│   ├── reference_library.py  # 参考资料库
│   ├── full_review.py        # 历史消息全量回顾
│   ├── reliability.py        # 投递重试与可靠性
│   ├── ops_store.py          # 运维状态
│   └── config.example.json    # 配置模板
├── 管理面板/
│   ├── panel_server.py       # Web 后端
│   ├── index.html            # 工作台主页
│   └── assets/               # 前端 JS / CSS
└── doc/                      # 设计文档
```

## Token 优化说明

默认配置已针对智谱免费额度做了优化：

- 每 120 秒批量分析一轮（而非每条消息单独调用）
- 单轮最多合并 6 个会话，system prompt 固定成本被摊薄
- 消息指纹缓存：无新消息不调 API
- 增量分析：已有历史分析的会话只处理增量消息

日常使用下日均 token 消耗约 15~20 万（含 system prompt），远低于每条消息单独调用的 50 万+。
