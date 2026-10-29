# 大肥鱼 · 卫戍协议社群机器人

跑在 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 上的 QQ 群机器人，服务《卫戍协议：盟约》同人游戏社群。
主仓库收纳**自研插件源码、运维脚本与文档**；早期独立项目「个人 QQ 重要消息提醒」仍保留在 `个人QQ重要消息提醒/` 与 `管理面板/`。

> ⚠️ 非官方同人作品，免费、严禁盈利。仓库内不含任何 API Key、SSH 私钥或线上数据库。

---

## 目录结构

```
.
├── plugins/                    AstrBot 插件源码（部署时拷进 data/plugins/）
│   ├── astrbot_plugin_dafeiyu_help/        说明书 /功能、上下文诊断 /ctxbudget
│   ├── astrbot_plugin_dafeiyu_daily_memory/ 情景记忆：原话留档、人物画像、会话标签
│   ├── astrbot_plugin_dafeiyu_cover/        翻唱（RVC 音色）
│   ├── astrbot_plugin_dafeiyu_group_guard/  群聊日额度 + 高峰休眠守卫
│   ├── astrbot_plugin_usage_view/           群内 /用量 查询
│   ├── astrbot_plugin_request_ledger/       逐请求用量账本
│   ├── astrbot_plugin_wushu_ops/            卫戍协议运营助手（FAQ、排行、服务器状态、报 bug）
│   ├── dafeiyu_runtime/                     插件共用运行时（计费、预算、选择器）
│   └── patches/                             对第三方插件的本地补丁
├── scripts/                    运维与开发脚本（按用途分目录）
│   ├── deploy/                 改服务器 / 容器 / 插件配置
│   ├── diagnostics/            只读排查（日志、缓存、记忆、预算）
│   ├── tests/                  本地与线上验证脚本
│   ├── cost/                   成本对账（官方账单 CSV ↔ 本地账本）
│   ├── voice/                  TTS / 翻唱 / 语音链路
│   ├── ops/                    定时运维（备份、依赖体检、内存加固）
│   ├── patches/                框架级补丁
│   └── cacheprobe/             缓存诊断插件源码
├── tests/                      单元测试（unittest）
├── docs/                       设计文档、优化方案、实施与回滚记录
├── compose/                    docker-compose 参考配置
├── BOT项目交接手册.md           系统全貌、成本口径、踩坑记录（接手先读）
├── 个人QQ重要消息提醒/          历史项目：QQ/微信重要消息筛选推送
└── 管理面板/                    历史项目：本地 Web 管理面板 + 本地 TTS 客户端
```

`vendor/`（第三方安装包、上游插件源码）与 `tmp/`、`review/`、`知识库/`、`TTS素材/` 等只存在于本机，已被 `.gitignore` 排除，克隆后不会出现。

---

## 快速开始

### 1. 运行 AstrBot

```bash
docker compose -f compose/docker-compose.yml up -d
```

镜像固定为 `soulter/astrbot:pinned-4.29.3`，**不要随意升级**：插件依赖该版本的 API 与计费行为。

### 2. 安装插件

把 `plugins/` 下的插件目录拷进容器数据卷：

```bash
cp -r plugins/astrbot_plugin_* /opt/bot-stack/astrbot/data/plugins/
```

`dafeiyu_runtime` 是共享库，放到持久化的 `data/pylibs/`，并确保容器 `PYTHONPATH` 包含它（见 `compose/docker-compose.yml`）：

```bash
cp -r plugins/dafeiyu_runtime /opt/bot-stack/astrbot/data/pylibs/
```

### 3. 配置

仓库只提供模板，实际配置需自行创建（均已被 `.gitignore` 排除）：

| 文件 | 来源模板 | 说明 |
|---|---|---|
| AstrBot 主配置 | 首次启动时由 WebUI 生成 | 填写模型 provider、平台适配器、预算策略 |
| `个人QQ重要消息提醒/config.json` | `config.example.json` | 历史项目配置 |
| `管理面板/本地TTS/config.json` | `config.example.json` | 本地 TTS 客户端与服务端地址 |

### 4. 跑测试

```bash
python -m unittest discover -s tests/plugin_optimization -v   # 109 项
python -m unittest discover -s tests/request_ledger -v        # 28 项
python tests/test_official_balance.py                         # 4 项
```

Windows 下 `zoneinfo` 需要 `pip install tzdata`，否则涉及时区的用例会报 `ZoneInfoNotFoundError`。

---

## 插件一览

| 插件 | 作用 |
|---|---|
| `astrbot_plugin_wushu_ops` | FAQ 知识库、排行榜、服务器状态、bug 提交与列表 |
| `astrbot_plugin_dafeiyu_help` | `/功能` 分类说明书；管理员 `/ctxbudget` 上下文诊断（不调模型） |
| `astrbot_plugin_dafeiyu_daily_memory` | 每日原话归档、人物画像、跨群记忆召回与会话标签 |
| `astrbot_plugin_dafeiyu_cover` | 点歌翻唱（RVC 音色） |
| `astrbot_plugin_dafeiyu_group_guard` | 群聊日额度 ¥0.5、工作日高峰休眠，超限静默 |
| `astrbot_plugin_usage_view` | `/用量`、`/账单` 查询 |
| `astrbot_plugin_request_ledger` | 逐请求记录用量，供官方账单对账 |

各插件的详细配置见其目录下的 `metadata.yaml` 与 `_conf_schema.json`。

---

## 文档

| 文档 | 内容 |
|---|---|
| [BOT项目交接手册.md](BOT项目交接手册.md) | 系统全貌、数据卷布局、成本口径、故障排查、30 秒摘要 |
| [docs/](docs/) | 缓存与固定开销优化、插件全面优化审查、实施与回滚记录 |
| [scripts/patches/README.md](scripts/patches/README.md) | 第三方插件补丁的适用方式 |
| [管理面板/README.md](管理面板/README.md) | 历史面板的启动方式 |

---

## 安全须知

- **不要提交** `密码.md`、`*.pem`、`secrets.dpapi`、`config.json`、任何 `*.db`；`.gitignore` 已覆盖，提交前仍建议 `git status` 复核。
- 机器人端**没有 shell 与写权限**，这是刻意设计：群消息不可信，防止 prompt injection。
- 线上改动前先备份（`原文件.bak-YYYYMMDD-HHMM`），回滚 = 恢复备份 + 重启对应服务。

---

## License

[MIT](LICENSE)