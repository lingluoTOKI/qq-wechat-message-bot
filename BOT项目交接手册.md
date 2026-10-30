# BOT 项目交接手册（大肥鱼 + 卫戍协议同人游戏服）

> **2026-10-29 后续实施更新**：已启用 astrbot_plugin_request_ledger，北京时间17:20:04起按DeepSeek实际HTTP请求采集usage，并导入官方114次请求／1.261369元快照。/用量、/账单分列官方与本地记录，不再使用固定覆盖率系数。旧成本面板仍是框架运行统计。日1元预算未提高，官方快照已经超过限额，后续付费模型请求因此暂停；规则查询仍可用。导入、范围、缺口和回滚见 [实施记录](docs/插件优化审查-2026-10-29/逐请求统计与官方校正实施记录.md)。以下旧快照不可作为当前完整状态。

> **用途**：给任何一个**没有历史上下文**的 AI 助手 / 协作者，在 3 分钟内建立对这套系统的正确认知，
> 知道「文件在哪、怎么连、哪些事不能乱动、哪些坑已经踩过」。
>
> **本版时间**：2026-10-29 16:20（**在 §14 基础上补入 §15「2026-10-29 傍晚：成本口径校正 + 记忆与分群 + 磁盘清理」**）
> **接手方式**：把本文 §12「30 秒摘要」整段贴给新对话当背景，需要细节时再让它读对应章节。
> **如果只关心「为什么今天改成这样」**，直接看 **§13（缓存）与 §15（成本口径 / 记忆）**。
> **如果只关心「现在到底哪些是确凿的、哪些还没验证」**，直接看 **§14 + §15.6**。

### 确定性标注约定（全文通用）

| 标记 | 含义 | 判定标准 |
|---|---|---|
| ✅ **已验证** | 有实测数据或可复现的日志/接口读数支撑 | provider_stats 真实计费口径、CacheGuard wire 分段对比、面板 API 返回值 |
| 🟡 **推断** | 现象与证据吻合，但缺直接验证 | 例如"命中率提升主要由关滑窗带来"——方向确定，归因比例未做对照实验 |
| ❓ **未验证 / 不确定** | 只是假设，或已知有异常但原因未定位 | **接手时优先处理这些** |
| ⛔ **已作废** | 曾是当时的结论，现已被推翻 | 见 §13.5 与各节的"作废"标注 |

> 规则：**不确定的结论不要写进"怎么改"里当依据**。§14 的两张表就是为此存在的。

---

## 0. 一句话画像 + 当前状态快照

**大肥鱼**是一个跑在 **AstrBot** 上的 QQ 群机器人，服务《**卫戍协议：盟约**》同人游戏社群。
机器人与游戏服跑在**同一台阿里云轻量服务器**上。非官方同人作品，免费、严禁盈利。

```
服务器    阿里云 SWAS 轻量，2核 / 3495MB 内存 / 无 GPU / 磁盘 40G（已用 65%，剩 14G）
域名      game.lingluotoki.dpdns.org  →  101.132.104.41
机器人    Docker 容器 astrbot（soulter/astrbot:pinned-4.29.3），端口 6185(WebUI)
游戏服    systemd stronghold(3000) + stronghold-3001(3001)，均 active
今日账单  官方口径 114 请求 / ¥1.261369（10-29 全天，官方用量导出）✅
成本对账  本地记录 76 行 / ¥0.827703 → 覆盖率 65.6%，**真实花费 ≈ 本地 ×1.52**（§4.4）
缓存命中  全天 84.1%；**最近单次稳定 85~95%**（重启后首次 10.8% 属正常重建）✅
单次构成  system 1.6k + tools 5.5k + 历史 7~11k + 记忆注入 1.8~3.0k（末尾，每轮未命中）
预算      日 3,000,000 token + ¥1.0；月 5,000 万 token + ¥10；超限行为 = stop ✅
群聊额度  每群每日 **¥0.5**（group_guard 守卫，账单估算口径）；私聊不受限 ✅
高峰休眠  工作日 09:00–12:00、14:00–18:00 群聊静默（×2 价时段），下班自动恢复 ✅
磁盘清理  cron 每日 04:10 跑 `clean-temp.sh`（temp 1 天前 + nginx 7 天前归档）
```

> ⚠️ 「全天 84.1%」是 10-29 累计值；**判断现在好不好，看最近 5 次请求**（`provider_stats` 口径）。
> 10-28 全天只有 73.8%，因为白天有搜索风暴 + 滑窗重算两次事故；改造是 17:19 之后才生效的。

**接手时必须知道的三件事**：
1. 这台机器只有 3.5GB 内存、无 GPU，**重启代价很高**（swap 颠簸、SSH 卡死、踢玩家）。
2. 机器人**没有任何 shell / 写权限**，这是刻意设计（群消息不可信，防 prompt injection）。
3. 改动前一律先 `cp 原文件 原文件.bak-日期-时间`，回滚 = cp 回去 + 重启对应服务。

---

## 1. ⚠️ 主机访问（凭据不写进文档）

| 项 | 值 |
|---|---|
| 机器 | 阿里云 SWAS 轻量，上海区，2 核 / 3495MB / 无 GPU |
| 公网域名 | `game.lingluotoki.dpdns.org`（→ `101.132.104.41`） |
| 内网 IP | `172.24.30.154` |
| SSH 用户 | **`root`** |
| SSH 私钥 | `F:\github\BOT用\robot.pem`（带口令） |
| 去口令副本 | `F:\github\BOT用\.robot-nopass.pem`（本机生成，**用完即删、勿提交**） |
| 口令存放 | `F:\github\BOT用\密码.md` |

> **‼️ 本文刻意不含口令、API Key、Token。**
> 旧版手册把 SSH 口令明文写在文档里，现已移除——
> 因为这份文档要贴进第三方 AI 对话，明文凭据一旦进入对话记录就等于泄露。
> 需要凭据时让**用户自己**从 `密码.md` 取，或由用户在本机执行命令。
>
> **同理**：`cmd_config.json` 里存着 DeepSeek / 小米 MiMo / 智谱三个 API Key。
> 需要看配置结构时**只打印键名，不要 dump 值**——上一轮就是因为 dump 配置导致三个 Key 暴露在对话里。

```bash
# 本机 Git Bash 连主机
ssh -i "/f/github/BOT用/.robot-nopass.pem" -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@game.lingluotoki.dpdns.org "命令"
```

---

## 2. 系统架构

```
QQ 群 ←→ 大肥鱼（AstrBot 容器）
              │
              ├─ 平台适配器：qq_official（唯一在用的，实际收发消息）
              │              aiocqhttp/snowluma（compose 里有，但容器当前未运行）
              │
              ├─ 15 个插件（详见 §3.2）
              │
              ├─ 模型 provider
              │    deepseek/deepseek-flash      ← 群聊回复（默认，唯一计入成本统计）
              │    xiaomi/mimo-v2.6-flash       ← 表情选图 / 记忆抽取 / 群管审核 / 语音语气
              │    xiaomi/mimo-v2.5-asr         ← 语音识别
              │    zhipu/embedding-3            ← 记忆向量化
              │
              └─ bot_proxy.py（172.18.0.1:31999，docker 私有网桥，只读）
                       ▲
                 游戏服状态（宿主机 systemd 服务，容器看不到进程，只能走这个桥）

游戏服侧：
  nginx(:80/:443)
    ├─ stronghold.service      :3000  主服 6人
    ├─ stronghold-3001.service :3001  契约服 4人
    ├─ stronghold-control      observe.py 内存监控
    └─ stronghold-bot-proxy    bot_proxy.py 只读状态桥
```

| 组件 | 形态 | 端口 | 备注 |
|---|---|---|---|
| AstrBot | Docker `astrbot` | 6185 WebUI / 6199 | 镜像 `soulter/astrbot:pinned-4.29.3`（**pin 死，别随便升级**） |
| 游戏主服 | systemd `stronghold` | 3000 | 入口 `/root/stronghold/server/net.js` |
| 游戏契约服 | systemd `stronghold-3001` | 3001 | MOD 定制 |
| 内存监控 | systemd `stronghold-control` | — | `/opt/stronghold-stability/observe.py` |
| 只读状态桥 | systemd `stronghold-bot-proxy` | 172.18.0.1:31999 | 机器人查宿主机状态唯一通道 |
| **本地语音代理** | systemd `local-tts` | 0.0.0.0:32000 | 给本机 GPT-SoVITS 派任务，详见 §6 |
| nginx | systemd `nginx` | 80/443 | 反代游戏服 + `/tts/` → 127.0.0.1:32000 |

> **snowluma 容器当前是停的**（`docker compose ps` 只有 astrbot），所以 `5099`(WebUI) 和 `6081`(noVNC) 没有监听。
> 机器人实际走 `qq_official` 平台，不缺它。**不要贸然拉起**——若它和官方平台同时接管同一条消息，会出现重复回复。

---

## 3. 关键文件位置（改前先备份）

备份习惯：`原文件名.bak-YYYYMMDD-HHMM`。

### 3.1 容器数据卷 `/opt/bot-stack/astrbot/data/` → 容器内 `/AstrBot/data/`

| 文件 | 作用 | 生效方式 |
|---|---|---|
| `cmd_config.json` | **AstrBot 主配置**（provider / platform / provider_settings / agent_runner） | **必须重启容器** |
| `data_v4.db` | 人格（`personas` 表）、偏好、内置定时任务（`cron_jobs`） | 人格需重启（内存缓存），见 §8 |
| `plugins/<插件名>/` | 15 个插件 | 改代码→重载插件；改 `_conf_schema.json`→重载插件 |
| `config/<插件名>_config.json` | 各插件配置 | 多数需重启 |
| `plugin_data/astrbot_plugin_cost_control/config.json` | **预算护栏**（token/费用/超限行为） | 重启容器 |
| `plugin_data/astrbot_plugin_cost_control/supplement.db` | **成本明细库**（含 `attribution` 归因与 `cache_events`），诊断金矿 | 只读查询 |
| `plugin_data/astrbot_plugin_livingmemory/livingmemory.db` | 长期记忆（`documents` 每日群聊日记 + `memory_atoms` 碎片） | 直读 |
| `_disabled_skills/` | 被移出的 4 个内置技能（见 §8.2） | 移回即恢复 |
| `pylibs/` | **持久化第三方依赖**（PYTHONPATH 指向这里，重启不丢） | 见 §8.4 |
| `patch/patch_astrbot_stats.py` | 框架 bug 补丁（compose `post_start` 自动应用） | 见 §8.5 |

### 3.2 已装插件（2026-10-28 实测 24 个）

```
astrbot_plugin_wushu_ops      自研：FAQ 知识库、服务器状态、项目问答工具
astrbot_plugin_cost_control   0.4.1 成本/预算/缓存诊断（attribution 极有用）
astrbot_plugin_usage_view     1.1.0 群里发「用量」查 token
astrbot_plugin_livingmemory   长期记忆（语义召回；top_k=3、min_importance_for_retrieval=0.3，注入走 extra_user_content）
astrbot_plugin_dafeiyu_help   说明书 1.5.0：/功能 + 真版 /ctxbudget（管理员诊断，0 token）
astrbot_plugin_dafeiyu_daily_memory  v0.3.1 情景记忆：原话留档 + 人物画像 + 事实 + **会话标签注入**（自研）
astrbot_plugin_dafeiyu_group_guard  v1.0.0 群聊日额度 ¥0.5 + 工作日高峰休眠（自研，见 §16）
astrbot_plugin_dafeiyu_cover  翻唱（RVC 预设 + 直调后端）
astrbot_plugin_dafeiyu_voice  语音（/语音开、档位 local/cloud）
astrbot_plugin_cache_guard    1.3.0 缓存诊断：前缀哈希 + 最终 HTTP JSON 分段对比（自装）
astrbot_plugin_kaomoji        颜文字（依赖人格里的 {{分类}} 标记）
meme_manager                  表情包（含情感模型选图）
astrbot_plugin_listen_music   点歌
astrbot_plugin_anysearch      联网搜索（唯一搜索入口，结果限流 5 条）
astrbot_plugin_bili_dynamic   B 站动态
astrbot_plugin_parser         消息解析
astrbot_plugin_proactive_chat 主动消息（直连 text_chat，已加预算前置判断）
spectrecore                   人格对话核心（🔴 已禁用）
astrbot_plugin_group_guardian 群管（🔴 已禁用 → 撤回/禁言/踢人全部失效，见 §13.6-3）
astrbot_plugin_clonetts       TTS 三层降级（🔴 已禁用，语音功能实际走 dafeiyu_voice）
astrbot_plugin_voice_clone_flow / astrbot_plugin_cacheprobe  🔴 已禁用/已删除
```

> **禁用清单在 `data_v4.db` 的 `preferences.inactivated_plugins`**，不在 cmd_config.json。
> 插件总数、启用状态可查面板 API：`GET /api/v1/plugins`。

### 3.3 宿主机关键路径

| 路径 | 作用 |
|---|---|
| `/opt/bot-stack/docker-compose.yml` | 容器编排（含 `post_start` 补丁 + `PIP_INDEX_URL` 阿里云镜像） |
| `/opt/bot-stack/bin/local_tts_server.py` | 本地语音代理（纯标准库） |
| `/opt/bot-stack/bin/local_tts_token.txt` | 代理访问令牌（600，勿外传） |
| `/opt/bot-stack/bin/deps_health_notify.py` | 依赖体检 + 配置守卫（cron 每 30 分钟） |
| `/opt/bot-stack/bin/clean-temp.sh` | 磁盘清理（cron 每日 04:10）：`data/temp` 删 1 天前文件、nginx 删 7 天前归档；**不碰 chat_history / db / 插件数据** |
| `/opt/bot-stack/logs/` | `deps-health.log` / `local-tts.log` / `clean-temp.log` / 用量汇总 |
| `/opt/stronghold-stability/start.mjs` | 游戏服入口 + 准入闸参数（**改要重启游戏服、会踢人**） |
| `/opt/stronghold-stability/observe.py` | 内存监控，`availableMB < 400` 告警 |
| `/opt/stronghold-stability/bot_proxy.py` | 只读状态桥 |

---

## 4. 模型分工与成本（**接手后最该先理解的**）

### 4.1 分工表（改变它要先想清楚）

| 用途 | 模型 | 说明 |
|---|---|---|
| 群聊回复 | `deepseek/deepseek-flash` | 默认 provider，**唯一计入成本统计的** |
| 表情选图 / 记忆抽取 / 群管审核 / 语音语气 | `xiaomi/mimo-v2.6-flash` | 插件直连，**不进面板统计** |
| 记忆向量化 | `zhipu/embedding-3` | 2048 维 |
| 语音识别 | `xiaomi/mimo-v2.5-asr` | ¥0.5/小时音频 |
| 语音合成 | 本机 GPT-SoVITS → MiMo → edge-tts | 见 §6 |

> DeepSeek 按**前缀缓存**计费：命中 ¥0.02/M，未命中 ¥1/M——**差 50 倍**。
> 所以「前缀是否稳定」比「省几个 token」重要得多。

### 4.2 今日实测：单次请求的固定开销

`attribution` 字段（在 `supplement.db` 的 `cost_supplements.attribution`）拆得很准：

| 维度 | 数值 | 来源 |
|---|---|---|
| `system` | **1624 或 2256** token | 人格 1233 + SafeMode 133 + 脚手架；多出来的 632 是 Skills 块（已解决） |
| `tools` | **1395 或 2595** token | 8 个白名单工具 vs 全量工具集 |
| `history` | 79~689 | 被 `max_turns` 压得很短 |
| `user` | 4~92 | |
| **`extra`** | **877~1930** token | **livingmemory 注入 3 条「每日群聊日记」**（`documents` 表，每条约 400 token） |

**关键结论**：`extra` 的来源**不是**记忆碎片（`memory_atoms` 平均只有 26 token），
而是 `documents` 表里每天 700~900 字的群聊日记。配置里**没有单条注入长度上限**，只能靠 `top_k` 控。

### 4.3 已做的成本优化（2026-10-26）

| 项 | 改动 | 效果 |
|---|---|---|
| Skills 块 | 人格 `skills=[]` + 目录层移走 4 个内置技能 | **省 632 token/请求**，已验证 |
| 人格精简 | 1558 → 1339 字（同时**加了**「对 TOKI 的占有欲」） | 净省 ~180 token |
| 记忆召回 | `recent_memory_count` 1→0 | 去掉 1 条**不看相关性**的强制注入 |
| 预算 | 日 600k/¥0.3 → **750k/¥0.45** | 今天下午曾因超限停摆 |
| 依赖持久化 | 5 个缺失依赖装进 `pylibs/` + requirements 补声明 | 重启不再丢 |
| pip 源 | compose 加 `PIP_INDEX_URL` 阿里云镜像 | 启动 110s → 87s（opencv 61MB 官方源会超时） |

### 4.4 官方账单口径（2026-10-29 校正，**旧成本数字全部作废**）

**唯一可信口径是 DeepSeek 平台「用量明细」导出的 CSV**（`amount-*.csv` + `cost-*.csv`）。
10-29 全天三套数字对照：

| 口径 | 请求 | 成本 | 状态 |
|---|---|---|---|
| 官方导出 | 76 | ¥0.6949 | ✅ 基准 |
| cost_control 面板 | 52 | ¥0.4963 | 🟡 覆盖率 **71.4%** |
| supplement.db 原始 `cost_amount` | 44 | ¥0.0549 | ⛔ 已作废（低估 11 倍） |

**官方单价（CNY / 百万 token）**：缓存命中 `0.02`、未命中 `1.0`、输出 `4.0`；
**工作日北京时间 09:00–12:00 与 14:00–18:00 全部 ×2**（0.04 / 2.0 / 8.0）。

**旧数字为什么错（双重根因，已修）**：
1. `price_selections` 指向 models.dev 候选价（≈真价 0.3 倍），**优先级压过手工 CNY 价**；
   已加最高优先级键 `deepseek/deepseek-flash|deepseek-flash`（CNY 1.0 / 0.02 / 4.0）。
2. AstrBot 内核按「一次 agent run」记一条统计（`_record_internal_agent_stats`），
   而官方按每次 API 请求计费（function-calling 多步各算一次）→ 请求数天然少记。
   已回填 `supplement.db` 928 条 `cost_amount`（含峰时 ×2、UTC→北京 +8）。

**折算用法（口径已定：只对账、不改内核）**：

```bash
# 本机：从官方导出 zip 对账，并累积历史系数
python "F:\github\BOT用\scripts\cost_reconcile.py" <官方zip> --save
# 输出：每日 官方req / 面板req / 覆盖率 / 官方¥ / 面板¥ / 成本覆盖率 + 近 7 日平均
```

- 结果写入 `F:\github\BOT用\scripts\cost_coverage.json`（按日累积）。
- **账单口径 ≈ 本地记录 × 系数**（当前 1.52，即覆盖率 65.6%）。
- 系数需随数据稳定：连跑 3 天以上再当准；若某天偏离 ±5%，先查插件/模型是否变动。
- ⚠️ 脚本默认读容器内路径；本机跑要用 `COST_DB` 指到拉下来的 `supplement.db`。

---

## 5. 缓存与「两种请求形态」（此前的最大谜团，已基本查清）

> **2026-10-28 补充**：本节讲的是「两种形态交替」的历史问题（已通过 persona 补 `tools` 修掉）。
> 当天更重要的结论是**滑动窗口与前缀缓存相斥**，以及由此做出的五项改造 → **见 §13**。
> §13 里有关于「为什么命中会突然掉到 20%」的完整解释与 wire 级证据。

### 5.1 两种形态交替

同一份 `attribution` 显示出两种固定形态来回切：

| 形态 | system | tools | 推测来源 |
|---|---|---|---|
| A | 2256 | 2595 | 从 `cmd_config.agent_runner.config.persona` 读人格（**该对象没有 tools/skills 键**）→ 退化成「全量工具 + 全部技能」 |
| B | 1624 | 1395 | 从 DB 读完整人格 → 8 工具白名单 |

框架根因（`astr_main_agent.py:628` / `:632`）：

```python
if persona and persona.get("skills") is not None:
    if not persona["skills"]:
        skills = []          # 显式空列表 → 不注入
    # persona 没有 skills 键 → 注入全部内置技能

if (persona and persona.get("tools") is None) or not persona:
    persona_toolset = tmgr.get_full_tool_set()   # → 全量工具
```

**Skills 那一半已经解决**（人格表 + 目录层双保险）。**tools 那一半还没定位**——
需要等群里有人说话，用 `astrbot_plugin_cacheprobe` 的调用栈回溯抓出调用方。

### 5.2 诊断工具（都在 `F:\github\BOT用\scripts\`）

> **2026-10-29 状态**：下面这些脚本仍可用，但**主力已换成 CacheGuard 1.3.0**（线上插件，
> 群里发 `/缓存状态` / `/缓存断点` / `/缓存链路` 就能看，wire 级分段对比，比离线脚本准）。
> `astrbot_plugin_cacheprobe` 已**删除**（它当时 dump 的 320K 聊天记录是安全隐患）；
> 它的能力已被 CacheGuard 覆盖。

| 脚本 | 用途 |
|---|---|
| `diag_supp_fields.py` | **最好用**：直接看 `attribution` 五维拆解 |
| `diag_cache_events.py` / `diag_recent_diff.py` | 看 `cache_events` 的 diff（注意早期事件缺 `system_diff` 字段，diff 会退化成「全部新增」） |
| `diag_cache_rate.py` | 缓存命中率分时段 |
| `measure_prompt.py` | 实测 system prompt 各组件 token |
| `diag_memory_tables.py` / `diag_memory_length.py` | 量记忆各表体积 |
| `disable_builtin_skills.py` / `clear_persona_skills.py` | §4.3 的 Skills 处理 |
| `apply_persona.py` | 写人格（带关键要素自检 + 整库备份） |

### 5.3 一个诊断陷阱

`cache_events` 里 `system_prompt_change` 有 198 条、`tools_change` 133 条——
**不要直接当成故障**。原因：

- 面板诊断按 `umo` 缓存上一轮签名，但**同一群下会跑多种请求**
  （主回复走 DeepSeek；记忆抽取/表情/审核走 MiMo，且没有人格）。
  不同 provider、不同用途的请求互相覆盖基准 → 大量「变更」是**跨请求类型比较产生的误报**。
- 每次**重启容器**都会让 `_last_ctx` 内存表清空，下一条必然被判成变更。
- 今天为了验证改动重启了 **5 次**，命中率因此一度掉到 44.7%（最近 1 小时）。
  **重启后第一条 0% 命中是物理必然，不是退化。**

---

## 6. 语音链路（三层降级，2026-10-26 新上线）

```
第 1 层  本机 GPT-SoVITS v2Pro（洛天依 13 周年模型，RTX 4060，RTF≈0.45）
            ↑ 电脑开机时走这层，音色 100% 原声、每次一致
第 2 层  MiMo TTS voiceclone（洛天依原声当参考音频）
            ↑ 电脑关机 / 8 秒无响应 / 限流时自动回落
第 3 层  edge-tts（zh-CN-XiaoyiNeural +15Hz，永久免费）
```

**关键设计：不预判电脑在不在。** 服务器发任务 → 8 秒没人领 → 自动回落。
所以开关机完全无感，不需要人工切换。

### 6.1 本机侧（用户的 Windows）

| 项 | 值 |
|---|---|
| GPT-SoVITS | `F:\github\GPT-SoVITS-v2pro-20250604\`（v2Pro 2025-06 整合包） |
| GPT 权重 | `GPT_weights_v2Pro\luotianyi13_e15.ckpt` |
| SoVITS 权重 | `SoVITS_weights_v2Pro\luotianyi13_e8_s384.pth` |
| 参考音频 | `refs\luotianyi13_ref.wav`（3.02s / 32kHz 单声道） |
| 参考文本 | `我现在需要一个长达三秒的语音` |
| 客户端 | `F:\github\BOT用\管理面板\本地TTS\local_tts_client.py` |
| 配置 | 同目录 `config.json`（面板可改） |

**⚠️ yaml 只有 `custom` 节生效**（`TTS.py` 里 `configs_.get("custom", ...)`），
且 `custom.version` 必须是 `v2Pro`，否则加载 v2Pro 微调权重会失败。

### 6.2 传输方式：**走 SSH 隧道，不走公网 HTTPS**

这是本项目最反直觉、但必须记住的一点：

```
本机 DNS 把 game.lingluotoki.dpdns.org 解析成 198.18.0.54（代理工具的 fake-ip）
而该域名在代理规则里走「直连」→ 直连 fake-ip → TLS 直接被断
   （现象：SSLEOFError / UNEXPECTED_EOF_WHILE_READING）
走系统代理 127.0.0.1:7897 也失败；真实 IP + SNI 也失败
```

**所以客户端优先走本地隧道**：`http://127.0.0.1:32000`（纯 HTTP over SSH，绕开 DNS/TLS/代理）。

- `启动管理面板.bat` 的 ssh 参数里有 `-L 32000:127.0.0.1:32000`
- 客户端 `_base()` 先探本地隧道、再探公网，断线时重新选路
- 面板的在线检测也改成隧道优先（否则页面会误报「未连接」）

### 6.3 面板一键启动

`管理面板 → 本机 → 本地语音 → 一键启动`：

```
后台线程：起 GPT-SoVITS api_v2.py → 轮询 9880 就绪（≤240s）→ 起客户端 → 连服务器
页面每 2 秒轮询 /local/tts/status 的 launch 字段看进度
停止 = 客户端 + GPT-SoVITS 一起停（释放显存）
```

实测：37 秒 API 就绪，41 秒全线 ready。

---

## 7. 机器人能力（大肥鱼会什么）

- **自动答疑**：群友直接问（不用 @），关键词命中 `plugins/astrbot_plugin_wushu_ops/faq.json`。
  ⚠️ 注意：2026-10-26 已从「命中就复读原文」改成**注入 system_prompt 让模型按人格作答**，
  并加了强弱关键词分级（4 字以上命中 1 个即触发，2-3 字需命中 2 个），冷却 600 秒。
  **原设计的关键词里混着「官方/免费/同人/模式/卡了」这类日常高频词，会乱插话——已修。**
- **`/服务器状态`**：主机内存/负载 + 服务端进程/内存/重启次数/在线玩家/挂机房间/准入延迟。
- **人格对话**：傲娇、短句、带颜文字（`{{分类}}` 标记由 kaomoji 插件替换成真颜文字）。
- **点歌 / 联网搜索 / 表情包 / 语音**。
- **主动消息**：间隔 3~16 小时，0-8 点静默。
- **群管**：大肥鱼是群管理员，撤回/禁言会真实执行（`auto_moderate_enabled=false`，
  仅保留 illegal_url + weapons 词库与刷屏撤回，阈值 20）。

---

## 8. 🚫 红线与踩过的坑（这一节最重要）

### 8.1 红线

1. **不要随意重启**。2GB 级小机器的重启会让 swap 颠簸、SSH 卡死。
   纯配置改动优先选「现读生效」；确需重启先问用户，并**成批做**（重启会清 prompt 缓存）。
2. **不要给机器人 shell / 写权限**。群消息不可信，LLM 持 shell = prompt injection 直出任意命令。
3. **凭据不进对话**。API Key、SSH 口令、`local_tts_token.txt`、`.robot-nopass.pem` 都不要 dump。
   查配置时**只打键名**。上一轮就是因为 dump `cmd_config.json` 让三个 Key 暴露在对话记录里，**至今未轮换**。
4. **备份优先**。任何线上文件改动前 `cp 原 .bak-日期-时间`。
5. **`docker compose up -d` 在 compose 配置没变时不会重建容器**——
   改完以为生效了其实没有。要重启用 `docker compose restart astrbot`。**这个坑踩过。**
6. **`data/plugins/` 目录里不要留备份目录 / 半成品**（会被当插件扫描并导入报错）；
   备份统一放 `/opt/bot-stack/backups/`。
7. **插件的 `@filter.command` / `@filter.on_llm_request` 装饰器必须写在插件自己的 `main.py`**，
   写在子模块（混入类）里框架不会调度 → 命令永远不触发或抛属性异常。
8. **面板保存配置 ≠ 运行中进程已生效**：`provider_settings` / `compression` 这类每请求读的会热生效，
   `admins_id` 这类必须重启。**改完要实测，别看文件。**
9. **装插件前先确认名字在 `star_registry` 里**，否则重载会退化成 reload-all，
   所有 handler 重注册 → 工具顺序变化 → 前缀缓存被重置。

### 8.2 Skills 注入（已解决，但要知道原因）

框架会往里注入 4 个内置技能（documents/pdf/skill-creator/spreadsheets）+ 7 条规则 ≈ 632 token，
而框架自己在该块末尾写着「**User has not enabled the Computer Use feature. You cannot use skills.**」
——**根本执行不了，纯浪费**。

处理方式（两道保险，都别撤）：
1. `personas.skills` 全部设为 `"[]"`（注意：该列存 JSON 文本，未设置时是**字符串 `"null"` 而非 SQL NULL**，
   `where skills is null` 会静默影响 0 行）
2. **目录层**：把 4 个技能目录移到 `/AstrBot/data/_disabled_skills/`
   —— 必须做，因为形态 A 的代码路径根本不读人格表的 `skills` 字段

### 8.3 人格改动必须重启

`personas` 表写完后，主路径走 `persona_manager.get_persona_v3_by_id()` 读的是**内存 `personas_v3`**
（启动时加载），**不是**每次查库的 `get_persona()`。所以改完必须重启容器。

### 8.4 依赖「重启即丢」

`edge-tts` 曾经不在 `clonetts/requirements.txt` 里（是容器内手装的），一次重启后语音全挂。
现在所有第三方依赖都装在**持久卷** `/AstrBot/data/pylibs`，compose 里 `PYTHONPATH` 指向它。

- 装依赖必须带 `--target /AstrBot/data/pylibs`
- **用阿里云 PyPI 镜像**：`-i https://mirrors.aliyun.com/pypi/simple/`（官方源拉 61MB 的 opencv 会超时）
- `deps_health_notify.py` 每 30 分钟体检一次，缺了就私聊告警 + 自动修配置开关

### 8.5 框架 bug 补丁

AstrBot v4.29.3 有个 bug：预算超限走「跳过 LLM」路径时
`ToolLoopAgentRunner` 的 `self.stats` 还没初始化就被访问 → `AttributeError` → 用户收到 "Error occurred"。

补丁在 `data/patch/patch_astrbot_stats.py`，通过 compose 的 `post_start` 钩子**每次启动自动应用**
（容器的改动默认随重建丢失，所以必须挂钩子）。**验证方式**：`docker exec astrbot grep -c "patch. stats-not-initialized" <文件>` 应 ≥1。

### 8.6 编码类坑（都踩过）

| 场景 | 坑 | 正确做法 |
|---|---|---|
| AstrBot 面板保存过的 JSON | 带 **UTF-8 BOM**，`encoding="utf-8"` 读会 `JSONDecodeError` | 读写都用 `utf-8-sig` |
| Windows `tasklist` / `taskkill` 输出 | **GBK**，`text=True` 按 UTF-8 解码会炸（异常还被 try 吞了 →「显示停了其实没停」） | `errors='replace'` |
| `启动管理面板.bat` | GBK 编码，改中文注释必须 `encode("gbk")` | 直接写 bytes 字面量会 `SyntaxError` |
| 权重文件名 | 中文 + 长破折号，API 走 query 参数传路径会编码出错 | 重命名成 ASCII |
| 人格 prompt | 写 markdown 粗体 `**...**`，模型可能原样带到群消息里 | 聊天人格 prompt **不要用 markdown 强调符** |
| SSH 与 bash | `pkill -f <名字>` 会匹配到自己的 shell 自杀 | 先 `ps | grep` 取 PID 再 kill |

### 8.7 其它

- **面板 POST 有同源校验**：脚本调 `/local/*` 必须带 `Origin: http://127.0.0.1:<port>`，否则 403。
- **面板超时不宜太短**：`_tts_server_online()` 原来 6 秒，会误报「离线」，已改 15 秒 + 重试。
- **`grep` 日志要配 `-v file_data`**：AstrBot 会打大量 base64，容易刷掉有用信息。

---

## 9. 待办 / 已知问题

| # | 事项 | 优先级 | 说明 |
|---|---|---|---|
| 1 | **四个 API Key 轮换** | **高** | DeepSeek / MiMo / 智谱 三个 provider Key，**外加 `provider_settings.websearch_deepseek_key`**（2026-10-28 读配置时第二次暴露，同样只在 `cmd_config.json` 里，改完重启容器）。 |
| 2 | ~~删除 `astrbot_plugin_cacheprobe`~~ | 中 | **已完成（2026-10-28 19:03）**：插件目录与 `plugin_data/cacheprobe`（320K dump）已删，备份 `data/backups/cacheprobe-20261028-1900.tar.gz`。后续要看工具数直接发 `/ctxbudget`。 |
| 3 | ~~形态 A 的调用方定位~~ | 中 | **已定位、修复并线上复验（2026-10-28 19:1x）**：根因是 `agent_runner.config.persona` 缺 `tools`/`skills`，框架回落到 `get_full_tool_set()` 导致同会话 20↔13 工具交替；已按 persona 12 补齐（备份 `cmd_config.json.bak-persona-tools-20261028-185047`）。`/ctxbudget` 连测两次：**工具 19 个、system 未变、工具 未变**，缓存占比 94%。 |
| 4 | 人格 `skills`/`tools` 是否还要精简 | 低 | `anysearch_*` 两个工具 48h 真实调用 0 次，但占 ~160 token；优先级低于 #3。 |
| 5 | snowluma 容器要不要恢复 | 低 | 当前只有 astrbot 在跑，机器人走 qq_official 正常。**拉起前先确认不会和官方平台重复接管消息。** |
| 6 | 参考音频只有 3.02 秒 | 低 | 在 GPT-SoVITS 建议下限（3~10s）。若音色不稳，换 5~10 秒干声会明显改善。 |
| 7 | ~~知识库 `kb.db` 仍空~~ | — | **已过时**：10-26 已导入 12 篇文档（21 个分块，`top_m_final=5`），单次注入 ≤1.5k 字。详见 §15.3 |
| 8 | 游戏 AI 队友「变肥鱼」 | 最低 | 游戏插件系统禁止改服务端逻辑，只能动 `lobby.js`/`botEmotes.js`，须低峰重启游戏服。 |
| 9 | **修 `/ctxbudget` 的管理员判定** | **高** | 20+ 次调用零回复、零 LLM 请求 → 被框架命令处理器接管后，内部管理员判定返回 False。`_is_admin()` 的三个候选值到底哪个与 `474496195`/`D3EB1FC…` 不匹配**未定位**。详见 §14.2-1。 |
| 10 | **金额闸门 ¥1.0 与 token 闸门口径不一致** | 中 | 10-29 全天**官方实际 ¥0.6949 / 76 请求**，占闸门 69%；峰时（14-18 点 ×2）仍可能触顶 → 触顶后机器人**静默装死**且无提示。见 §13.6-4 / §15.2 |
| 11 | **确认群管插件是否有意禁用** | 中 | `astrbot_plugin_group_guardian` 禁用中 → 撤回/禁言/踢人/群公告全部失效。见 §13.6-3。 |
| 12 | `compression.provider_id` 改成 MiMo 的风险 | 中 | MiMo 窗口小，52.5k 触发的压缩请求可能失败 → 退回滑窗、命中掉回去。**至今未触发，风险未验证**。见 §14.2-3。 |
| 13 | 主动消息（proactive_chat）预算补丁未被观测到生效 | 低 | 补丁已上线，但今天没触发过"超限跳过"日志。见 §14.2-6。 |
| 14 | **旧记忆没有来源标签**（跨群共享池） | 低 | 现无串群实证（§15.4 严格比对 24h = 0 条）；若真出现，再给 50 条活跃记忆按置信度打标（目标脚本已跑通，约 6 成可判） |
| 15 | 删除 `soulter/astrbot:backup-4.29.1`（2.8G 回滚镜像） | 低 | 4.29.3 才跑几小时，建议观察一周再删；删了可再腾 2.8G |
| 16 | 每日记忆插件**不能热重载**（有后台任务，terminate 会挂住 API） | 低 | 改它的配置/代码只能 `docker restart astrbot`（约 25 秒） |

---

## 10. 常用命令

```bash
# 连主机
ssh -i "/f/github/BOT用/.robot-nopass.pem" -o StrictHostKeyChecking=no root@game.lingluotoki.dpdns.org "uptime; free -m"

# 机器人
cd /opt/bot-stack && docker compose ps
cd /opt/bot-stack && docker compose restart astrbot          # 改配置后（约 15 秒就绪）
docker compose logs --since=10m astrbot 2>/dev/null | grep -iE "\[ERRO|Traceback" | grep -v file_data

# 今日用量 / 预算
docker exec astrbot python /tmp/_bud.py                     # 或直接读 plugin_data 下的 config.json
docker exec astrbot python /tmp/dcr.py                      # 缓存命中率分时段

# 语音链路
systemctl status local-tts
curl -s -H "X-TTSToken: $(cat /opt/bot-stack/bin/local_tts_token.txt)" http://127.0.0.1:32000/health

# 游戏服（⚠️ 会踢人）
systemctl restart stronghold stronghold-3001
cat /run/stronghold-stability/admission-3000.json           # 看准入闸生效参数

# 依赖体检（手动跑一次）
python3 /opt/bot-stack/bin/deps_health_notify.py
```

### 10.1 2026-10-28 新增：诊断与热改（都在容器内跑）

```bash
# 看最近请求的真实缓存命中（provider 口径）
docker exec astrbot python -c "
import sqlite3,datetime
c=sqlite3.connect('file:/AstrBot/data/data_v4.db?mode=ro',uri=True)
for st,i,ca,o in list(c.execute('select start_time,token_input_other,token_input_cached,token_output from provider_stats order by start_time desc limit 8'))[::-1]:
    d=datetime.datetime.fromtimestamp(st,datetime.timezone.utc)+datetime.timedelta(hours=8)
    print(d.strftime('%H:%M:%S'),'总=%d 缓存=%d 未命中=%d'%(i+ca,ca,i))
"

# 面板 API 热改配置（会走 save_config + 热应用）
#   GET/PUT /api/v1/config-profiles/default   body=完整 config dict
#   JWT 用 dashboard.jwt_secret 自签，payload={\"username\": ...}
# 插件精准重载：POST /api/plugin/reload（单数）  body={\"name\": \"插件名\"}

# 查插件启用状态
docker exec astrbot python -c "
import json,urllib.request,hmac,hashlib,base64,time
s=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']['jwt_secret']
b=lambda x: base64.urlsafe_b64encode(x).rstrip(b'=')
h=b(json.dumps({'alg':'HS256','typ':'JWT'}).encode()); p=b(json.dumps({'username':'astrbot','exp':int(time.time())+300}).encode())
t=(h+b'.'+p+b'.'+b(hmac.new(s.encode(),h+b'.'+p,hashlib.sha256).digest())).decode()
d=json.loads(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:6185/api/v1/plugins',headers={'Authorization':'Bearer '+t})).read())
print([x['name'] for x in d['data']])
"
```

> ⚠️ `docker logs` 的输出里混着二进制分隔符，grep 时加 `-a`（`grep -a 'xxx'`），否则会漏行。

---

## 11. 给接手 AI 的工作方式建议

1. **先要数据，再下结论。** 这套系统里大多数「看起来是 bug」的现象，
   都是「某种设计的副作用」或「跨请求类型比较的误报」。先看 `attribution`、看 `cache_events`、看日志时间线。
2. **改完必须验证，不能只看配置。** 例如人格改完要重启（内存缓存）；
   Skills 改完要看 `list_skills()` 是否真的返回 0；预算改完要确认 `on_exceeded` 行为。
3. **一个改动成批做完。** 每次重启都会清 prompt 缓存、命中率掉一截。今天为验证改了 5 次、重启 5 次，
   命中率从 67% 掉到 44%——**代价是真实的，别一项一项试**。
4. **别信指标能证明听感。** 音色一致性用任何频谱指标都证明不了（LTAS 对语音内容太敏感：
   同句重复 0.87，不同句子只有 0.67）。**直接出样音让人听。**
5. **不确定时问用户，不要猜着改。** 尤其是：重启游戏服、拉起 snowluma、动 provider 配置。
6. **2026-10-28 补充：先算账再改。** 那天每个改动都先估了「省多少 token / 多少 ¥」，
   才决定做不做；结果是五项改动（关滑窗、关内置搜索、限流、top_k、诊断）合计把命中从 65% 拉到 91%。
   反例是中途花在 webchat 面板接口、命令解析这些"边角"上的时间——**先做影响面最大的那一项**。
7. **改完要用"框架的真实读数"验证，不要用眼睛看。**
   `provider_stats`（真实计费口径）、CacheGuard 的 wire 分段对比、`/缓存状态`——都比日志文本可靠。

---

## 12. 30 秒摘要（直接整段贴给新对话）

> 我在维护「大肥鱼」——一个跑在 **AstrBot** 上的 QQ 群机器人（AstrBot 容器 `astrbot`，
> 镜像 `soulter/astrbot:pinned-4.29.3`），服务于同人游戏《卫戍协议：盟约》社群。
> 机器人和游戏服同跑在一台**阿里云 2 核 / 3.5GB / 无 GPU** 的轻量服务器上，
> 域名 `game.lingluotoki.dpdns.org`（root 登录，私钥 `F:\github\BOT用\robot.pem`，
> 去口令副本 `.robot-nopass.pem`，口令见同目录 `密码.md`——**凭据别贴进对话**）。
>
> **模型分工**：群聊回复 `deepseek-flash`（唯一计费的，按前缀缓存计费，命中/未命中差 50 倍）；
> 表情、记忆抽取、群管审核、语音语气走 `xiaomi/mimo-v2.6-flash`；向量化 `zhipu/embedding-3`；
> 语音识别 `mimo-v2.5-asr`。
>
> **预算**：日 300 万 token + ¥1.0，超限直接停回复（`on_exceeded=stop`），次日 0 点刷新。
> ⚠️ 金额闸门 ¥1.0 与 token 闸门口径不一致，10-28 曾因触顶让机器人**静默装死**（群里表现为"怎么不回你了"）。
>
> **成本计法（10-29 定，旧数字作废）**：本地记录只覆盖 **65.6%**，**真实花费 ≈ 本地 ×1.52**
> （10-29 官方 ¥1.261369 / 114 请求，本地 ¥0.827703 / 76 行）。官方单价 CNY：缓存 0.02、未命中 1.0、
> 输出 4.0 / 百万 token，工作日 09–12 与 14–18 点 ×2。每次导出官方 zip 跑
> `scripts/cost/cost_reconcile.py <zip> --save` 更新系数（详见 §4.4 / §15.2）。
>
> **缓存（10-28 改造后，10-29 复核通过 ✅）**：`compression.max_turns = -1` 关掉了滑动窗口——滑窗与前缀缓存天生相斥，
> 丢最老一轮就会让历史起点改写、20k 历史每 1~2 轮重算。system/tools 段钉死不动，
> 记忆/时间戳只塞最后一条消息。命中率已从 25~65% 提到 **85~95%**（10-29 全天 84.1%）。
> 内置 `web_search` 已关（10-29 全天 0 次调用）、anysearch 限流 5 条、livingmemory `top_k=3`（extra 3038→1700 token）。
> 诊断用 CacheGuard 1.3.0 的 `/缓存状态`、`/缓存断点`（wire 级分段对比）。详见 §13、§14。
>
> **⚠️ `/ctxbudget` 目前是哑的**：被框架命令处理器接管后管理员判定失效，20+ 次调用零回复（❓ 原因未定位，见 §14.2-1）。
> **文档约定**：本文用 ✅已验证 / 🟡推断 / ❓未验证 / ⛔已作废 四档标注确定性，
> **动手前先看 §14 的两张表**，别拿"推断"当"事实"。
>
> **语音**：三层降级——本机 GPT-SoVITS v2Pro（洛天依 13 周年模型，RTX 4060）→ MiMo voiceclone → edge-tts。
> 服务器起 `local-tts` 代理（32000），本机客户端**走 SSH 隧道** `http://127.0.0.1:32000` 连它
> （因为这台机器直连该域名会被 fake-ip 代理规则打断 TLS）。
>
> **知识库**：`/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/faq.json`（现读生效）。
> ⚠️ 内置 RAG 库只有 21 个分块、`top_m_final=5`，单次最多注入 ~1.5k 字；**不是整份注入**
> （`知识库/卫戍协议/knowledge.md` 那 17,772 字线上没有任何代码读取，是死文件，见 §15.3）。
>
> **记忆与分群**：长期记忆**跨群共享**（`memory_scope_mode=global`，122 条全在 `livingmemory:global`），
> 每轮注入 1.8–3.0k token（挂在末尾，不影响缓存前缀）；`min_importance_for_retrieval` 已从 0.12 调到 **0.3**。
> 每日情景记忆插件 v0.3.1 已注入「当前会话」标签（群别名在 `session_labels`），她已能答对自己在哪个群；
> 近 24h 严格比对**未见串群证据**（§15.4）。
> **成本诊断**：`plugin_data/astrbot_plugin_cost_control/supplement.db` 的 `attribution` 字段最有用。
>
> **红线**：① 3.5GB 小机器别乱重启（换 prompt 缓存、踢玩家）；
> ② 不给机器人 shell/写权限（防 prompt injection）；
> ③ 凭据不进对话；
> ④ 改前先 `cp .bak-日期-时间`；
> ⑤ `docker compose up -d` 在配置未变时不重建容器，要重启用 `restart`。
>
> 详细内容见 `F:\github\BOT用\BOT项目交接手册.md`。

---

## 13. 2026-10-28 缓存专项改造纪要（当天最重要的一轮）

> 这一节是**当天的主线**：把缓存命中率从 25~65% 拉到 90%+。
> 下面每一条都是**改过的真实配置**，接手后请先照抄现状，再决定要不要动。

### 13.1 问题是怎么定位的

现象：下午群里聊天，DeepSeek 返回的 `cached_tokens` 忽高忽低（7% / 25% / 78% / 92% 交替），
但 `provider_stats` 里 `token_input_cached` 明明在涨。

工具链：
- **CacheGuard 1.3.0**（新装，`/AstrBot/data/plugins/astrbot_plugin_cache_guard/`）：
  它有两个层次——① `on_llm_request` 里比较 system/工具/历史的哈希；② `wire_diag.py`
  **只读捕获发往 api.deepseek.com 的最终 HTTP JSON**，按 `model / instructions / tools / input / other`
  分段做带盐哈希对比，直接告诉你**第几段、第几个字段变了**。不记录正文、不改请求。
- 命令：`/缓存状态`（单会话命中率）、`/缓存总体`（管理员，全局）、`/缓存诊断`、`/缓存链路`、`/缓存断点`。

**决定性证据**（wire 级）：

```
seq=2  sections=input  input_break=1   path=messages[1].content[0].text   ← 历史头部被改写
seq=3  sections=input  input_break=15  path=messages[15].content[2].text  ← 只是尾部追加（正常）
tool_break=None   → 工具段从头到尾没变过
```

结论：**不是缓存坏了，是前缀被改写了**。

### 13.2 为什么「滑动窗口」和「前缀缓存」天生相斥

DeepSeek 按**前缀**缓存：只有**逐字节相同**的开头才命中。请求结构是

```
system (≈2.3k) → tools (≈2.5k) → history (15k~28k) → 本轮新消息
```

`compression.max_turns = 8` 的语义是「**只保留最近 8 轮，超出就把最老的一轮丢掉**」。
一旦丢掉最老的一轮，**历史的起点就变了** → 从 messages[1] 起全部与上次不同 → 20k 历史每 1~2 轮重算一遍，
命中直接掉到 20~30%。

> **一句话**：静态的钉死，动态的全往后推。
> system 和工具定义连顺序都不能动；记忆、时间戳、画像这类每轮变的东西，只能塞进**最后一条消息**。

### 13.3 实际改了哪五处

| # | 位置 | 改动 | 为什么 | 生效方式 |
|---|---|---|---|---|
| 1 | `cmd_config.json` → `agent_runner.config.compression.max_turns` | `8 → -1` | **关掉滑动窗口**，历史只增不减 → 前缀纯追加 | 热生效（每请求重读） |
| 2 | `cmd_config.json` → `provider_settings.web_search` | `true → false` | 关掉 AstrBot 内置 `web_search_deepseek`（**每次 ¥0.005 按次计费**） | 热生效 |
| 3 | `plugins/astrbot_plugin_anysearch/tools.py` → `_clamp_max_results` | `100 → 5` | 搜索结果不再大到被截断，模型看得见内容就不会换措辞反复重搜 | 需重载插件 |
| 4 | `config/astrbot_plugin_livingmemory_config.json` | `recall_engine.top_k: 6 → 3`；`agent_tools.agentic_recall_max_result_chars: 12000 → 4000` | extra 注入 3,038 → 1,700 token/轮（-44%）；第二条是防「一次主动召回拉回巨量内容」 | 需重载插件 |
| 5 | `agent_runner.config.misc.max_steps` | `128 → 16`（10-28 早上已改） | 防止搜索风暴跑到几十步 | 重启 |

另外做了三件"周边"的事：
- **装 CacheGuard 1.3.0**（诊断）+ 给它打补丁 `scripts/patches/patch_cache_guard_prediction.py`：
  原版把「token 预测」绑死在 `trim_mode=observed_guard` 上，`trim_mode=off` 时永远显示 `uncalibrated`。
  补丁把**预测与裁剪解耦**（预测照常算、裁剪仍受开关控制），并加预测误差统计 → 实测误差 **0.1%~4.6%**。
- **自建真版 `/ctxbudget`**：`astrbot_plugin_dafeiyu_help/ctxbudget.py`（1.5.0）。
  原先群里的 `/ctxbudget` **根本不存在**，命令落进 LLM → 模型照着历史旧格式**编诊断**
  （编造 hash、编造"缓存占比 89.2%"），每次烧 2~3k token，还把群里的人带偏。
  现在：管理员 → 返回**真实读数**（system/工具/历史/临时注入构成 + provider_stats 的真实命中占比）；
  非管理员 → **静默忽略**；未注册的斜杠命令 → 非管理员静默、管理员回"未知命令"。**全部 0 token**。
- **proactive_chat 超限前置跳过**：`scripts/patches/patch_proactive_chat_budget.py`。
  该插件用 `provider.text_chat()` 直连，**绕过 cost_control 的钩子** → 超限后照发、且不记账。
  补丁让它发之前先自查 token + 金额两个维度，超限就静默跳过。

### 13.4 现在的配置快照（照抄即可）

```jsonc
// cmd_config.json
"agent_runner": {
  "config": {
    "compression": {
      "max_turns": -1,                    // 关滑窗（关键！）
      "trim_turns": 1,
      "overflow_strategy": "llm_compress",// 到 64k×0.82≈52.5k 自动 LLM 摘要（⚠️ 至今未触发）
      "keep_recent_ratio": 0.25,
      "provider_id": "xiaomi/mimo-v2.6-flash",   // ← 用户后来改成了 MiMo，见 §13.6-1 / §14.2-3
      "fallback_max_tokens": 16000        // 死配置（provider 自己配了 max_context_tokens）
    },
    "misc": { "max_steps": 16, "tool_schema_mode": "skills_like", ... }
  }
},
"provider_settings": { "web_search": false, "datetime_system_prompt": false },
"admins_id": ["D3EB1FC111219D62DA6C12702EC52055", "474496195"]
// provider["deepseek/deepseek-flash"].max_context_tokens = 64000  ← 真正的上下文闸门
```

```jsonc
// plugin_data/astrbot_plugin_cost_control/config.json（10-29 实测）
"budgets":      { "per_session_daily": 3000000, "global_daily": 3000000, "global_monthly": 50000000 }
"budgets_cost": { "global_daily": 1.0, "global_monthly": 10.0 }
```

### 13.5 今天新踩的坑（**都很贵，别再踩**）

1. **`admins_id` 填错格式 → 全员不是管理员。**
   框架判定在 `core/pipeline/waking_check/stage.py:83`：`str(event.get_sender_id()) == admin_id`。
   qq_official 的 `get_sender_id()` 返回**QQ 号**（TOKI = 474496195），而面板里填的常是 **openid**
   （`D3EB1FC…`）→ 永远匹配不上 → 所有 admin 命令一律"权限不足"。
   **两个都填**（现在配置里就是两个），并且**改完要重启**：面板保存只写文件，**不热更新运行中进程的配置对象**。
2. **混入类（mixin）里的 handler 框架不调度。**
   `@filter.command` 装饰器写在 `ctxbudget.py` 时，handler 归属该**子模块**，
   而框架只调度**插件自身模块**的 handler → 命令永远不触发，还抛
   `'DafeiyuHelpPlugin' object has no attribute 'send_ctxbudget'`。
   **正确写法：装饰器必须写在插件的 `main.py` 里，实现放子模块也行，但装饰器要回到 main。**
3. **装插件失败一次后，重载会走 reload-all。**
   `star_manager.reload(不存在的名字)` → 退化成"重载所有" → 所有 handler Remove 再 Add →
   **工具注册顺序变化 → 前缀重置**（那次命中率当场掉到 25%）。
   **装新插件前先确保名字在 `star_registry` 里，再精准重载。**
4. **`plugins/` 目录里不要留备份目录**：会被当成插件扫描并导入报错
   （`No module named 'data.plugins.cache_guard_v1'`）。备份放 `/opt/bot-stack/backups/`。
5. **qq_official 的 At 标记是方括号格式** `[At:qq_official]`，不是 `<@xxx>`；
   解析命令要同时兼容两者，还要兼容 `"/ cmd"`（斜杠后带空格）。
6. **"缓存命中率低"和"缓存坏了"是两回事**。判断方法：看 `wire_diag` 的 `input_break` 位置
   —— 断点在历史头部 = 滑窗；断点在 system/tools = 有人改了它们。
7. **面板 API 热改配置有边界**：`provider_settings.web_search` 这种**每请求读**的热生效；
   `admins_id` 这类**启动/调度期持有**的不会，必须重启。

### 13.6 遗留问题 / 风险（10-29 复核后的最新状态）

> **确定/不确定的完整对照见 §14**。下表只列状态变化。

| # | 问题 | 影响 | 最新状态（10-29） | 建议 |
|---|---|---|---|---|
| 1 | **`compression.provider_id` 已改成 MiMo** | MiMo 的上下文窗口远小于 52k，**压缩请求本身可能超限失败**；失败会退回 `truncate_by_turns`（滑窗回来，命中又掉） | ❓ **至今一次都没触发**，风险未验证 | 触发压缩时盯日志；若失败率高，改回 `deepseek/deepseek-flash` |
| 2 | `/ctxbudget` 旧 handler 残留 | 与新 handler 抢同一条命令、抛属性异常 | ✅ **已解决**：自检显示只有 `dg_ctxbudget_*@main`，无残留 | — |
| 2b | **`/ctxbudget` 现在对管理员也是哑的** | 20+ 次调用零回复、零 LLM 请求 → 被框架命令处理器接管后，内部管理员判定返回 False | ❓ **现象已确认、原因未定位** | 见 §14.2-1：把 `_is_admin()` 的三个候选值打出来定位 |
| 3 | **群管插件 `astrbot_plugin_group_guardian` 处于禁用状态** | 撤回 / 禁言 / 踢人 / 群公告等 20 多项功能**全部失效**（大肥鱼本来是群管理员，是会真执行的） | ❓ 仍禁用，**是否有意未确认** | 问用户。要用就恢复（会带 20 多个工具 schema 回工具段） |
| 4 | 金额闸门 ¥1.0 与 token 闸门**口径不一致** | 10-28 18:02 触顶，机器人**静默装死** 6 次，且**拦截时不发任何提示** | ⚠️ 10-29 15:35 已用 ¥0.594/¥1.0（59%），晚高峰仍有触顶风险；token 闸门已被用户抬到 **300 万** | 金额上限抬到 ¥1.5；或关掉金额闸门只留 token；**并给拦截加一句提示** |
| 5 | `fallback_max_tokens=16000` 是死配置 | 误导人 | ⛔ 仍未改 | 改成 64000（纯文档性，不影响行为） |
| 6 | 生日记忆串号 | 大肥鱼被记成"10月25日"（那是耀白的生日），TOKI 当场纠正过 | ❓ 未修 | 情景记忆抽取的"引用消息主语归属"规则待修 |
| 7 | `datetime_system_prompt=false` | 模型失去"当前时间"感知 | ✅ 有意为之（换命中率） | 若需要时间，改由插件在**消息末尾**注入（不影响前缀） |

### 13.7 什么时候必须重启 vs 热生效

| 改动 | 生效方式 |
|---|---|
| `cmd_config.json` 里的 `provider_settings`（web_search、datetime_system_prompt） | **热生效**（面板 API `PUT /api/v1/config-profiles/default`） |
| `cmd_config.json` 里的 `agent_runner.config.compression.*` | **热生效**（每请求重读） |
| `cmd_config.json` 里的 `admins_id` | **必须重启** |
| 插件的 `_conf_schema.json` / `config.json` | **重载该插件**即可 |
| 插件的 `.py` 代码 | **重载该插件**；但**装新插件 / 首次加载失败后重载 = reload-all**，会重置工具顺序 |
| `persona`（人格） | **必须重启**（内存缓存） |

> 面板 API 正确姿势（比手改文件安全，会走 `save_config` + 热应用）：
> `GET/PUT /api/v1/config-profiles/default`，body 是**完整 config dict**。
> JWT 用 `dashboard.jwt_secret` 自签，payload 是 `{"username": ...}`（不是 `sub`）。
> 插件重载：`POST /api/plugin/reload`（**单数**），body `{"name": "插件名"}`。

---

## 14. 2026-10-29 复核：哪些确定、哪些不确定

> 复核时间：2026-10-29 15:50。数据源：`provider_stats`（真实计费口径）、
> CacheGuard wire 分段对比、面板 API、插件日志、`data_v4.db` 的 `preferences`。
> **本节是接手时的第一读物**——先知道哪些结论可以拿来改代码，哪些不能。

### 14.1 ✅ 已验证（可以当事实用）

| # | 结论 | 证据 |
|---|---|---|
| 1 | **缓存命中率现在是 85~95%** | 10-29 最近 12 次请求：81.7 / 87.2 / 87.1 / 91.0 / **10.8** / 81.0 / 88.4 / 90.2 / 90.3 / 95.0 / 89.5 / 88.3 %；全天累计 84.1% |
| 2 | **那次 10.8% 是容器重启后的第一次请求**（前缀重建），不是故障 | 容器 15:37 重启；14:55:36 是重启后首条，14:56:31 就回到 81.0% |
| 3 | **`max_turns=-1`（关滑动窗口）已生效** | `cmd_config.json` 读数 `compression.max_turns = -1`；CacheGuard 报 `history_shared` 持续为 `N/N→N+2`（纯追加） |
| 4 | **历史在增长且缓存同步在涨**（不是靠压历史换命中） | 总输入 10.6k→28.7k，缓存 1.1k→25.3k |
| 5 | **内置 `web_search` 已彻底关闭** | 10-29 全天 `使用工具：web_search_deepseek` = **0 次**；只剩 anysearch（2 次）+ anysearch_extract（1 次） |
| 6 | **anysearch 结果限流 5 条生效** | 同上，搜索调用 3 次，未见超长结果引发的重搜 |
| 7 | **livingmemory `top_k=3` 生效，extra 注入瘦身 44%** | CacheGuard `temp_est`：改造前 3,038 → 改造后 1,700 token/轮 |
| 8 | **CacheGuard 1.3.0 在线可用** | wire 诊断已抓 47 次最终请求快照（`已抓取=47`），`/缓存状态`「/缓存断点」「/缓存链路」正常返回 |
| 9 | **预算上限现为 日 300 万 token + ¥1.0** | `budgets.global_daily=3000000`、`budgets_cost.global_daily=1.0`（10-28 是 200 万，10-29 用户改到 300 万） |
| 10 | **禁用插件共 6 个** | `preferences.inactivated_plugins`：GPT_SoVITS、voice_clone_flow、clonetts、cacheprobe、**group_guardian**、spectrecore |
| 11 | **`admins_id` 两个 ID 都在配置里** | `["D3EB1FC111219D62DA6C12702EC52055", "474496195"]`（QQ 号 + openid 都填了） |
| 12 | **handler 归属问题已解决** | 插件自检日志：`dg_ctxbudget_gate@main、dg_ctxbudget_capture@main、dg_ctxbudget_cmd@main、help_cmd@main`——**没有 `@ctxbudget` 残留**了 |
| 13 | **llm_compress 至今一次都没触发** | 今日最大请求总输入 54,455，但 `should_compress` 按 messages 估算（约 49k）未过阈值 52.5k；日志里搜不到压缩调用 |
| 14 | **dashboard 的 `anyio.EndOfStream` 报错是噪声** | 客户端主动断开导致（面板刷新/关页），不影响机器人主流程 |
| 15 | **自有插件源码已纳入 Git** | `plugins/`（自有插件与 `dafeiyu_runtime`、`patches/`）+ `scripts/` 按用途分目录；第三方安装包在本地 `vendor/astrbot/`，已被 `.gitignore` 整目录排除 |

### 14.2 ❓ 不确定 / 待处理（**接手优先看这里**）

| # | 问题 | 已确定的现象 | 未确定的部分 | 建议动作 |
|---|---|---|---|---|
| 1 | **`/ctxbudget` 对管理员也是静默的** | 10-28 18:31 → 10-29 15:14 期间群里发了 **20+ 次** `/ctxbudget`（斜杠 / 带空格 / 带 @ 各种写法），**一次都没回**；且这些消息**没有产生任何 LLM 请求**（最近一次 LLM 是 14:55:36，14:50 那条无请求）→ 说明**被框架的命令处理器接管了**，而不是落进模型 | 为什么我们的 `dg_ctxbudget_cmd` 判定"非管理员"？`_is_admin()` 内部比对 `get_sender_id()` / `message_obj.sender_id` / `message_obj.sender.user_id`，**到底哪个值与 `474496195` / `D3EB1FC…` 不匹配没定位** | 在 `_is_admin()` 里打一行 INFO，把三个候选值和判定结果打出来；跑一次 `/ctxbudget` 即可定位。**修好之前，这条命令对所有人都是哑的** |
| 2 | **`/ctxbudget` 注册成框架命令后，非管理员静默这条设计未实测** | 设计意图是"非管理员静默、管理员给真数据"；因为 #1 没修好，**两条分支都没验证过** | 静默是否真的 0 token（没有 LLM 请求这点可确认 ✅）；管理员分支完全没跑过 | 修 #1 时一并验 |
| 3 | **llm_compress 触发时会不会失败** | 配置 `provider_id = xiaomi/mimo-v2.6-flash`（用户 10-28 改的）；触发阈值 64,000×0.82 ≈ 52.5k | MiMo 的上下文窗口**远小于** 52.5k → 摘要请求本身可能超限。**从未触发过，所以完全未验证** | 触发时盯日志；失败会退回 `truncate_by_turns`（滑窗回来，命中会掉）。若频繁失败，把 `provider_id` 改回 `deepseek/deepseek-flash` |
| 4 | **金额闸门 ¥1.0 是否够用** | 10-29 15:35 累计 ¥0.594（61 请求）；09 点那小时就花了 ¥0.215（峰时 ×2） | 晚高峰（14-18 点 ×2）跑满后会不会再次触顶装死——10-28 18:02 确实装死过 6 次 | 傍晚再看一下 `budgets_cost` 占比；若接近 1.0 就抬到 ¥1.5，或干脆关掉金额闸门只留 token 闸门 |
| 5 | **群管插件被禁用是有意还是误关** | `group_guardian` 在禁用列表里 | 撤回 / 禁言 / 踢人 / 群公告**全部失效**（大肥鱼本来是群管理员，是会真执行的） | 问用户。若要恢复，注意它会带 20+ 个工具 schema 回工具段（对命中略不利，但可接受） |
| 6 | **主动消息（proactive_chat）的预算补丁没被观测到生效** | 补丁已上线（`scripts/patches/patch_proactive_chat_budget.py`） | 今天没有触发过"超限跳过"的日志；补丁逻辑用我们自己的计价（与 cost_control 口径不同） | 观察 21:00 后的排程日志 |
| 7 | **生日记忆串号** | 大肥鱼被记成"10月25日"（那是耀白的生日）；TOKI 当场纠正过 | 情景记忆抽取里"引用消息的主语归属"规则尚未修 | 改 `astrbot_plugin_dafeiyu_daily_memory/extract.py` 的引用归属规则 |
| 8 | **面板的 webchat / live-chat 自测通道始终没跑通** | `POST /api/v1/chat/threads` 因 `parent_message_id` / `selected_text` 校验一直 422 | 所以**任何插件改动都只能靠群里真人发消息来验证**，没有自动化回路 | 若要自动化自测，得先把 webchat 的 thread 创建参数搞对 |
| 9 | **"anysearch 48h 调用 0 次"已过时** | 10-29 有 3 次搜索调用 | 无 | 已在 §3.2 标注 |

### 14.3 🟡 只做了推断、没做对照实验的结论

| 结论 | 推断强度 | 说明 |
|---|---|---|
| "命中率 65% → 91% 主要是关滑窗带来的" | 🟡 中 | 方向确定（wire 证据显示断点在 messages 头部）；但**没有做单变量对照**（比如只回滚一项再看）。同批改动还有 extra 瘦身、关内置搜索、限流，三者都有贡献 |
| "extra 瘦身后每轮省 ~1,300 token" | 🟡 中高 | CacheGuard `temp_est` 实测 3,038 → 1,700；但请求总量同期也在变 |
| "关掉内置 web_search 不影响回答质量" | ❓ 低 | 今天只用了 3 次搜索，没有需要多次检索的复杂问题，**没有对照样本** |

### 14.4 今天补做的确认动作（可复现）

```bash
# ① 看最近 12 次请求的真实命中（改任何东西之后都跑一次）
docker exec astrbot python -c "
import sqlite3,datetime
c=sqlite3.connect('file:/AstrBot/data/data_v4.db?mode=ro',uri=True)
for st,i,ca,o in list(c.execute('select start_time,token_input_other,token_input_cached,token_output from provider_stats order by start_time desc limit 12'))[::-1]:
    d=datetime.datetime.fromtimestamp(st,datetime.timezone.utc)+datetime.timedelta(hours=8)
    t=(i or 0)+(ca or 0)
    print(d.strftime('%m-%d %H:%M:%S'),'总=%-6d 缓存=%-6d 未命中=%-5d 命中=%.1f%%'%(t,ca or 0,i or 0,(ca or 0)*100/t if t else 0))
"

# ② 查当前生效配置（只打键名，别 dump 值）
docker exec astrbot python -c "
import json
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))
print('compression',json.dumps(d['agent_runner']['config']['compression'],ensure_ascii=False))
print('web_search',d['provider_settings'].get('web_search'),'| datetime',d['provider_settings'].get('datetime_system_prompt'))
print('admins_id',d.get('admins_id'))
c=json.load(open('/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json',encoding='utf-8-sig'))
print('budgets',json.dumps(c['budgets'],ensure_ascii=False)); print('budgets_cost',json.dumps(c['budgets_cost'],ensure_ascii=False))
"

# ③ 看禁用插件与插件自检
docker exec astrbot python -c "
import sqlite3
c=sqlite3.connect('file:/AstrBot/data/data_v4.db?mode=ro',uri=True)
for r in c.execute('select value from preferences where key=\"inactivated_plugins\"'): print(r[0])
"
docker logs --since 30m astrbot 2>&1 | grep -a '本插件 handler'
```

---

## 15. 2026-10-29 傍晚：成本口径校正 + 记忆与分群 + 磁盘清理

> 这一轮的起因是**用户拿官方用量导出核对，发现面板数字差 11 倍**。
> 结论：旧成本数字全部作废，改用官方账单；同时确认「知识库整份注入」这条旧判断是错的。

### 15.1 本轮改动清单（都可回滚）

| 时间 | 动作 | 结果 | 备份 |
|---|---|---|---|
| 14:10 | 修价格优先级 + 回填 `supplement.db` 928 条 | 面板开始按真价计费 | `config.json.bak-20261029-141049`、`supplement.db.bak-20261029-141304` |
| 15:12 | livingmemory `min_importance_for_retrieval` 0.12 → **0.3** | 低价值闲聊不再被召回（只影响检索，不影响入库） | `..._config.json.bak-imp-20261029-151228` |
| 15:2x | 磁盘清理 | 释放 ≈3.7G，磁盘 74% → 65% | — |
| 15:3x | `daily_memory` **v0.3.1**：注入「当前会话」标签 | 两个群都能答对自己在哪 | 源码改动（`main.py` + `_conf_schema.json`） |
| 15:47 | 写 `session_labels` 群别名 + 重启 | 标签显示群名而非末 6 位 ID | `astrbot_plugin_dafeiyu_daily_memory_config.json.bak-label-20261029-1549` |
| 次日 04:10 | `clean-temp.sh` cron | temp/nginx 自动清理 | — |

**注意**：这轮重启了 2 次（15:37 打补丁、15:47 写别名），每次重启后第一条请求必为冷缓存（10.8% / 4.9%），属正常重建，不要当故障。

### 15.2 对账折算（用户已定：只对账、不改内核）

详见 §4.4。要点：**账单 ≈ 本地记录 × 覆盖率倒数**，当前系数 1.52。脚本 `scripts/cost/cost_reconcile.py`，
历史累积在 `scripts/cost_coverage.json`。覆盖率只有 65.6% 不是"少算了钱"，是内核按 agent run 记账的结构性缺口；
补到 95% 需改 AstrBot 内核，用户明确放弃。

**每日例行动作**：导出官方 zip → 跑脚本 `--save` → 看系数是否漂移（±5% 内视为正常）。

### 15.3 知识库注入真相（原 P0-1 作废）

| 说法 | 实况 |
|---|---|
| 「每轮整份注入 knowledge.md 6.8k 字」 | ⛔ **假**。`knowledge.md`（17,772 字、20 章节）**线上没有任何代码读取**；`search_knowledge()` 只被测试脚本调用 |
| 真实检索 | ✅ AstrBot 原生 KB：12 篇小 txt / 21 个 chunk，`top_m_final=5`，单次最多 ≈1.5k 字（≈1k token） |
| FAQ 命中路径 | ✅ 直接插短答案，并**移除 `query_project_info` 工具**，只发 1 次请求 |

所以「只注入命中章节」最多再省 ~700 token/次 ≈ **¥0.0007/次**，日均 30 次也就 ¥0.02/天。
**不建议做**：跨章节问题（素材导入+省流量、房间上限+无人房清理）会答歪，代价远大于收益。

### 15.4 记忆与分群（结构性事实 + 实测）

**结构**：

| 项 | 值 |
|---|---|
| 记忆范围 | `memory_scope_mode = global`，`use_session_filtering = false` |
| `memory_atoms` | 122 条，全部 `session_id = livingmemory:global`（47 active / 72 archived / 3 expired） |
| 参与检索的会话 | 3 个：群A `34CD…`（676 次请求）、私聊（192）、群B `B307…`（63） |
| 每轮注入 | 2 条"最近记忆" + 最多 3 条相似记忆 ≈ **1.8–3.0k token**，挂在消息末尾（不动前缀） |
| 召回参数 | `top_k=3`、`min_similarity=0.32`、`min_importance=0.3`（本轮已调） |

**群别名（`session_labels`，改后需重启）**：

```
34CD410881D4258E0F4FC55B1CE87003=卫戍协议测试
B3077F0FCFC1F29CBD8B7A5C083F0434=虚拟的未来会梦到电子天依吗？
```

**实测**：

- ✅ 两个群问「你现在在哪个群」，都答对（15:49 实测原话）。
- ✅ 严格比对近 24h：按「4-gram 只在一个会话出现过」的标准，检查另一会话的回复 → **命中 0 条**。
  （群A 独有 25,606 / 群B 2,802 / 私聊 5,403 个片段）
- 🟡 旧记忆归属试探：50 条活跃记忆按「写入时刻 ±窗口 + 文字重叠」归因 → 43 条判给群A、7 条无匹配；
  高置信 29 条。**这只是推断，且私聊/群聊同人同话题时无法区分。**
- ❓ 因此**暂不给旧记忆打标**（打错反而制造新错）。新记忆也还没带来源标签。

**若以后真的出现串群**：优先方案是给 50 条活跃记忆打 `【群名】` / `【来源未记录】`，
再改 livingmemory 写入路径让新记忆自带来源（需重启，且是第三方插件，升级要重打）。

### 15.5 磁盘

| 项 | 变化 |
|---|---|
| 初始 | 40G 用 28G（74%），剩 9.9G |
| 清理 | `motricseven7/snowluma` 镜像 + 退出容器 ≈3.1G、`data/temp` 1 天前 ≈580M、nginx 7 天前归档 56M |
| 现状 | 用 24G（**65%**），剩 **14G** |
| 防回涨 | cron 每日 04:10（避开 03:00 备份），日志 `/opt/bot-stack/logs/clean-temp.log` |

**没删**：`soulter/astrbot:backup-4.29.1`（2.8G 回滚镜像，建议观察一周）、`chat_history/images` 743M（近 7 天，聊天记录在用）。

### 15.6 本轮遗留（❓）

| # | 项 | 说明 |
|---|---|---|
| 1 | 旧记忆无来源标签 | 现无串群实证，等真出现再处理（§15.4） |
| 2 | `backup-4.29.1` 镜像 2.8G | 观察 4.29.3 稳定后再删 |
| 3 | `daily_memory` 不能热重载 | 有后台任务，`terminate` 会挂住 reload API（返回 500）→ 只能重启 |
| 4 | 记忆检索阈值只调了检索侧 | 0.2 重要度的闲聊仍会入库，只是不再召回 |
| 5 | `/ctxbudget` 仍哑、金额闸门 ¥1.0 仍紧、llm_compress 仍未触发 | 见 §14.2-1 / §13.6-4 / §14.2-3 |

---

## 16. 群聊日额度 + 高峰休眠（`dafeiyu_group_guard` v1.0.0）

> 2026-10-29 20:00 上线。目标：群聊每天最多只花 ¥0.5，高峰时段（双倍价）直接不开口，私聊完全放开。
> 实现是**独立小插件**（约 160 行），不改进程内其他插件，也不碰 AstrBot 内核。

### 16.1 规则（三层，从上到下依次生效）

| 顺序 | 规则 | 触发条件 | 行为 |
|---|---|---|---|
| 1 | **高峰休眠** | 工作日 09:00–12:00 / 14:00–18:00（北京） | 群聊不回复，发一次休眠提示；私聊照常 |
| 2 | **群日额度** | 该群当日累计 ≥ ¥0.5（账单估算） | 当日该群不再回复，发一次额度提示；私聊照常 |
| 3 | 全局兜底 | cost_control `global_daily` ¥1.0 | 整机停（既有逻辑，未改动） |

- **不补答**：休眠/超额期间收到的消息直接 `stop_event()` 丢弃，**不排队**；恢复后只回新消息。
- **额度按群独立**：群 A 花满 0.5 不影响群 B；私聊不计入群额度。
- **生效起点**：`effective_from`（默认 10-29 20:00）之后的花费才计入当天，避免上线瞬间因历史花费被静默。

### 16.2 高峰时间表与恢复节点

| 时段 | 起 | 止 | 倍率 |
|---|---|---|---|
| 上午峰 | 09:00 | **12:00（恢复）** | ×2 |
| 平峰 | 12:00 | 14:00 | ×1 |
| 午后峰 | 14:00 | **18:00（恢复）** | ×2 |
| 夜间平峰 | 18:00 | 次日 09:00 | ×1 |

- 每天切换 4 次：09:00 睡、12:00 醒、14:00 睡、18:00 醒。
- **周六/周日与法定节假日全天不睡**（`peak_weekdays_only=true`）。
- **恢复后首条请求必是冷缓存**（约 24k token ≈ **¥0.03**），属正常，不要当故障。

### 16.3 配置参数（`data/plugin_data/astrbot_plugin_dafeiyu_group_guard/config.json`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 总开关 |
| `group_daily_budget_cny` | 0.5 | 每群每日额度（账单估算，CNY） |
| `private_exempt` | true | 私聊跳过全部限制 |
| `panel_to_bill` | 1.52 | 本地行合计 → 账单的折算系数。基数取 `supplement.db` 逐行 `cost_amount`；官方 114 请求 ¥1.261369 / 本地 76 行 ¥0.827703 → 覆盖率 65.6%，故 ×1.52 |
| `effective_from` | `2026-10-29T20:00:00+08:00` | 当日额度起算点（含时区） |
| `peak_sleep` | true | 高峰休眠开关 |
| `peak_windows` | `[[9,12],[14,18]]` | 高峰窗口（北京时间，左闭右开） |
| `peak_weekdays_only` | true | 仅工作日 |
| `notice_peak` / `notice_budget` | 见文件 | 提示文案，`{end}` / `{budget}` 会被替换 |

**改配置不用重启**：守卫每次请求都重读 `config.json`；改**代码**才要 `docker restart astrbot`。

### 16.4 异常处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 群里完全不回话，也没提示 | 当日额度或高峰判定命中，但提示已被本窗口去重 | 看 `docker logs astrbot \| grep group_guard` 是否有「群额度用尽」；否则查 `enabled` |
| 高峰时段仍然回话 | ① 非工作日 ② 容器时钟偏移 ③ `peak_sleep=false` | `docker exec astrbot date` 对时；检查 `timedatectl` |
| 额度明明没用完却停了 | `effective_from` 写成历史时间，把当天早些时候的花费算进去了 | 改 `effective_from` 为当前时刻 |
| 额度一直不触发 | `supplement.db` 没有新行（cost_control 未记录） | 核对 `select max(created_at) from cost_supplements` 是否随聊天增长 |
| 私聊也被拦 | umo 判定异常（不含 `GroupMessage` 才会放行） | 看日志里的 umo 原文；确认是 `FriendMessage` |
| 插件加载即报错 | `main.py` 语法/路径 | 重启后 `grep -Ei 'Traceback\|group_guard'` |

**设计原则**：读耗时数据失败时**放行**（fail-open），只记 warning，不让守卫因数据库锁把整机卡死。

### 16.5 验证命令（容器内）

```bash
# 当前时刻判定 + 各会话当日花费
docker exec -i astrbot python3 - < /tmp/verify_guard.py
# 期望：当前(非高峰) 回复=正常；假设 10:30 → 群聊「停止」、私聊「正常」
```

单元测试覆盖：09:00/11:59/12:00/12:30/15:00/18:00/周六/08:59 八个边界，全部 PASS。

### 16.6 回滚

1. 临时停用：`config.json` 里 `"enabled": false`（立即生效，无需重启）。
2. 彻底移除：`rm -rf .../plugins/astrbot_plugin_dafeiyu_group_guard` + `docker restart astrbot`。

---

## 17. 2026-10-30：Self-Learning 评估结论 + 自建「风格学习」插件开发规格（可交给外部 AI 实现）

> 本节用途：回答「要不要用 AstrBot 市场上的 self-learning 插件」，并给出不冲突的自建方案。
> 结论先行：**不启用第三方 self-learning；改为自建 `astrbot_plugin_dafeiyu_style`**，只做「表达风格 + 黑话 + few-shot」，
> 记忆/回复/预算/人格一律不动。下方 §17.16 是可直接粘贴给 GPT 的实现指令。

### 17.1 结论（✅ 已定）

| 问题 | 结论 |
|---|---|
| 第三方 `self-learning` 4.4.4 要不要开 | ❌ **不在线上容器启用**；仅可在本机/独立测试容器做价值验证 |
| 智谱能不能用 | ✅ 能，但**只用免费档**（GLM-4.5-Flash）；付费档会脱离现有账本体系 |
| 自建是否更好 | ✅ 更适合本机：内存增量 <30MB、零双写、走现有预算门、无 AGPL 传染 |
| 自建功能边界 | 只做风格规则 + 黑话 + 用户→机器人配对示例；**不做**记忆/情绪/好感度/图谱/回复决策 |

### 17.2 第三方插件评估（`NickCharlie/astrbot_plugin_self_learning`）

- 版本 4.4.4，AGPL-3.0，作者 NickMo + EterUltimate，支持 aiocqhttp / qq_official / telegram，要求 AstrBot ≥ 4.11.4（✅ 与线上 4.29.3 兼容）。
- 代码体量约 10MB：`main.py` 24KB、`config.py` 43KB、`_conf_schema.json` 42KB、25 个 repository、17 个 ORM 模块、独立 Quart WebUI（端口 7833）。✅ 已通读主仓库。
- 能力：消息抓取 → 双层 LLM 筛选/提炼 → 待审队列 → few-shot/黑话/社交上下文注入；另有情感/好感度/情绪演化/LightRAG/Mem0 等可选能力。

**与本仓库的重叠（✅ 读代码确认）**

| 能力 | 现有实现 | self-learning | 冲突 |
|---|---|---|---|
| 群聊留档/画像 | `daily_memory`（`memory.db` 的 `events` 表） | 自建 sqlite 再抓一遍 | **双写** |
| Prompt 注入 | `daily_memory` 两个 `on_llm_request`（priority 96 临时 part + 95 system_prompt 会话标签） | 6 路上下文并行注入 | token 重复、口径混乱 |
| 回复决策 | AstrBot 主流水线 | 目标驱动对话/本地回复器 | 可能抢回复 |
| 预算 | `group_guard` + `request_ledger` | 后台学习绕过 `on_llm_request` 闸门 | **成本盲区** |
| 人格 | 人格提示词文件人工维护 | `auto_apply_approved_persona` | 可覆盖 system_prompt |

**成本体系缺口（✅ 逐文件确认）**

- `dafeiyu_runtime/request_capture.py` 默认 `allowed_hosts=['api.deepseek.com']` → 其他域名请求不记账。
- `dafeiyu_runtime/request_ledger.py` 计价只认 `deepseek-flash`；CSV 对账导入强制单账户、单 key、单模型。
- `dafeiyu_runtime/balance.py` 余额查询写死 `api.deepseek.com/user/balance`。
- 结论：任何非 DeepSeek 付费模型都会造成「面板/账本看不到的花费」，必须先改共享运行时，代价不值得。

**资源占用（🟡 估算，需实测校准）**

| 项 | 估算 |
|---|---|
| 内存增量 | 80–200MB（含 WebUI 线程 20–50MB）；若点「全能力依赖」再 +300MB 以上 |
| 启动 | +5–15s（建表/补列/迁移） |
| CPU | 静默≈0；批量学习窗口 10–60s 尖峰；2 核与游戏服同机 |
| 磁盘 | 3–6MB/天（3 群×500 条估），月 100–180MB；剩余 14G 可用但需清理策略 |
| 回复延迟 | `enable_llm_hooks` 单源超时默认 3.0s，最坏 +3s/条 |

**线上门禁（✅ 已定，暂不执行）**：关闭状态零负担（禁用插件不 import）；若确需线上试，必须满足 `availableMB ≥ 900MB`、只装「基础能力依赖」、`db_type=sqlite`、V2 全 legacy、hook 超时 ≤1.2s、学习窗口 03:00、且先 `pip freeze` 存档（requirements 含 `fastapi>=0.124`，有升级内核依赖的风险）。

### 17.3 智谱适配结论（✅ 方案 / 🟡 价格）

- 接口：OpenAI 兼容，`base_url=https://open.bigmodel.cn/api/paas/v4/`；AstrBot 按 openai 兼容 provider 配为 `chat_completion` 即可。
- 模型分工：粗筛 `GLM-4.5-Flash`（官方标称免费、128K 上下文）；提炼可选 `GLM-4.5`（输入 0.8 元/M、输出 2 元/M）。⚠️ 价格会变，以官网为准。
- 必须关闭 thinking 模式，否则 JSON 解析容易失败（插件/自建都吃纯 JSON）。
- 现有栈已有 `zhipu/embedding-3` ✅，账户可复用；但**智谱付费调用不在账本内**，所以只允许免费档。

### 17.4 自建插件定义（✅ 需求）

**名称**：`astrbot_plugin_dafeiyu_style`（显示名「大肥鱼表达风格」）

**目标**：从已有群聊归档里提炼「怎么说」，以极低成本注入回复，让机器人说话像群里的人。

**非目标（明确禁止）**：
1. 不采集/不保存聊天原文（只读 `daily_memory` 的库）；
2. 不改 `req.system_prompt`；
3. 不自动修改人格文件；
4. 不做回复决策、不发主动消息；
5. 不引入 PostgreSQL / WebUI / 新 pip 依赖；
6. 不新增任何非 DeepSeek 的付费模型调用。

### 17.5 数据源（✅ 已确认）

- `daily_memory` 的库：容器内 `/AstrBot/data/plugin_data/astrbot_plugin_dafeiyu_daily_memory/memory.db`
- 只读打开：`sqlite3.connect("file:<path>?mode=ro", uri=True, timeout=5)`
- 只用 `events` 表：

| 列 | 用途 |
|---|---|
| `id` | 增量游标（`meta.last_event_id`） |
| `session_id` / `group_id` | 分群（空 `group_id` = 私聊，默认跳过） |
| `sender_id` / `sender_name` | 过滤自身（`is_self=1` 不学）与配对示例 |
| `content` / `ts` | 文本与时间（长度 6–300 字才参与） |

### 17.6 自己的库（`style.db`，与 memory.db 完全独立）

```sql
CREATE TABLE IF NOT EXISTS style_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id   TEXT NOT NULL,
  rule       TEXT NOT NULL,
  evidence   TEXT,
  status     TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
  created_at REAL NOT NULL,
  reviewed_at REAL, reviewed_by TEXT,
  used_count INTEGER DEFAULT 0, last_used_at REAL
);
CREATE TABLE IF NOT EXISTS jargon (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL, term TEXT NOT NULL, meaning TEXT NOT NULL,
  confidence REAL DEFAULT 0.7, status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  UNIQUE(group_id, term)
);
CREATE TABLE IF NOT EXISTS few_shots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL, user_text TEXT NOT NULL, bot_reply TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL, target_id INTEGER NOT NULL,
  action TEXT NOT NULL, actor TEXT, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);  -- last_event_id / fail_days
```

规则：仅 `approved` 参与注入；`pending` 只进审批列表；`rejected` 永不注入（保留 30 天后可清理）。

### 17.7 学习流程（每日一次，避开高峰）

1. 03:00 触发（不依赖 cron，插件内 `asyncio` 循环 + 当前小时判断即可）。
2. 读 `events` 中 `id > meta.last_event_id` 且 `group_id` 命中配置、`is_self=0`、长度 6–300 的消息，取 `max_batch` 条。
3. 消息数 < `min_messages` → 本轮跳过，不推进游标。
4. **两段式**（必须）：
   - 粗筛：弱模型（GLM-4.5-Flash）判断哪些消息含稳定表达习惯/黑话/可复用问答，输出 `{"keep":[id...]}`；
   - 提炼：强模型（可同一模型）输出 `{"rules":[{...}],"jargon":[{...}],"few_shots":[{...}]}`，每条必须带 `evidence` 原文片段（≤120 字）。
5. JSON 解析失败 → 修复一次（去 ```json 包裹/截断尾部）→ 仍失败则整批丢弃并记日志，**不重试第二次**。
6. 写入 `pending`；每群每日新规则上限 `daily_rule_cap`（默认 8），总量上限 `total_rule_cap`（默认 60），超限丢弃最旧 `pending`。
7. 成功才推进 `meta.last_event_id`；连续 2 天失败 → 暂停学习并在日志告警。

### 17.8 注入规格（✅ 复用现有写法）

```python
# 与 daily_memory 相同：临时 part，不动 system_prompt
from astrbot.core.agent.message import TextPart
req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
```

- `@filter.on_llm_request(priority=97)`（daily_memory 是 96/95，这里排在其后）。
- 内容：`<style>` 包裹，≤ `inject_rules`（5）条规则 + ≤ `inject_shots`（3）对示例，总长 ≤ `inject_max_chars`（600，约 300 token）。
- 固定声明：「以下仅作表达参考，禁止复述、禁止改变事实与立场」。
- `asyncio.wait_for(..., timeout=hook_timeout_s=1.2)`；任何异常 → `logger.debug` + 返回，不注入、不阻塞回复。
- 取不到群 `group_id`（私聊）→ 不注入。

### 17.9 命令（管理员，未知命令静默）

| 命令 | 行为 |
|---|---|
| `/风格 状态` | 学习开关、游标、待审数量、最近一次运行时间与 token（只报数字） |
| `/风格 列表` | 列出 pending（每条 `<id> <规则摘要> <证据摘要>`，每页 10 条） |
| `/风格 通过 <id>` | 置 approved，写 `review_log` |
| `/风格 拒绝 <id>` | 置 rejected |
| `/风格 黑话` | 列出 pending 黑话（同一状态机） |
| `/风格 导出` | 输出 approved 规则/黑话/示例的 JSON 文本，便于交接给外部 AI 复盘 |

### 17.10 配置（`data/plugin_data/astrbot_plugin_dafeiyu_style/config.json`，热读取）

```json
{
  "enabled": true,
  "groups": [],
  "hour": 3,
  "coarse_provider_id": "<GLM-4.5-Flash 的 provider id>",
  "refine_provider_id": "<同上或更强的免费模型>",
  "min_msg_len": 6, "max_msg_len": 300,
  "min_messages": 30, "max_batch": 100,
  "daily_rule_cap": 8, "total_rule_cap": 60,
  "inject_rules": 5, "inject_shots": 3, "inject_max_chars": 600,
  "hook_timeout_s": 1.2,
  "admin_only": true
}
```

- `groups: []` 表示全部群（与 self-learning 白名单「空=全量」语义一致）；测试期必须显式只填测试群。
- 配置读取失败时用内置默认值，不阻塞插件加载。

### 17.11 预算与失败降级（✅ 必须实现）

- 所有模型调用必须包在 `await guarded_model_call(ctx.llm_generate, ...)` 中（`dafeiyu_runtime/paid.py`）。
- 捕获 `ModelBudgetBlocked` → 本轮直接跳过（日志一行，不重试）；这就是自建相对第三方插件的核心优势：**后台学习也受预算门约束**。
- 无 `coarse_provider_id` / `refine_provider_id` → 插件只读不学，`/风格 状态` 明确提示缺 provider。
- `memory.db` 不存在/被锁 → 静默跳过（`logger.debug`），不影响回复。

### 17.12 资源预算与上线门禁

| 项 | 目标值 |
|---|---|
| RSS 增量 | < 30MB |
| 启动增量 | < 0.5s（不做 DB 全表扫描，只建表 + 读游标） |
| 磁盘 | < 50MB（规则/黑话/示例，正文只存 ≤120 字证据） |
| 每日模型调用 | 1 次/群（可合并为一次批量调用） |
| 注入延迟 | ≤ 1.2s，超时静默 |
| 成本 | GLM-4.5-Flash 免费档 = ¥0；若改付费档 🟡 ≈ ¥0.01–0.03/天，需先改账本 |

门禁：仅当 `availableMB ≥ 600MB` 时开启；开启后 24h 内 `docker stats` RSS 增量 > 50MB 立即回滚。

### 17.13 目录结构与文件职责（交给实现者）

```
plugins/astrbot_plugin_dafeiyu_style/
├── metadata.yaml      # name/display_name/version/author 遵循现有 8 个插件格式
├── main.py            # Star 注册、命令路由、on_llm_request、定时学习循环
├── config.py          # 默认值 + 热读取 + 校验（不引入 pydantic）
├── store.py           # style.db DDL 与读写（SQL 只出现在这里）
├── learning.py        # 增量读 memory.db（只读）→ 两段式 → 写 pending
├── prompt.py          # 粗筛/提炼提示词常量（纯文本，禁止动态拼人格）
├── inject.py          # 规则 + 示例拼装、长度截断、超时保护
└── (可选) README.md   # 仅写用法与回滚，不复述本手册
tests/test_dafeiyu_style.py
```

复用约定（不要重写）：`dafeiyu_runtime.paid.guarded_model_call`、`dafeiyu_runtime.context.claim_feature`（若未来需要互斥）、`StarTools.get_data_dir(PLUGIN)`。

### 17.14 测试与验收（✅ 必须过）

单元测试（`tests/`，unittest）：
1. JSON 解析：正常 / ```json 包裹 / 截断尾部 / 完全非法 → 最后一律不抛异常；
2. 上限：规则数、单条长度、注入总字符截断；
3. 状态机：pending→approved→注入；pending→rejected→永不注入；
4. 断库：`memory.db` 缺失/只读打开失败 → 学习跳过且插件加载成功；
5. 预算阻断：`guarded_model_call` 抛 `ModelBudgetBlocked` → 游标不推进、无写入；
6. 注入异常：拼装抛错 → 回复流程不受影响。

线上验收顺序：
1. 装插件 → `/风格 状态` 有回显；
2. 手动改 `hour` 到当前+1 分钟触发一轮（或提供 `/风格 跑一次` 管理员命令）→ 看 `pending` 出现；
3. `/风格 通过 <id>` → 下一条相关回复的请求里出现 `<style>` 段（用 `/ctxbudget` 核对 token 增量 ≤300）；
4. `docker stats` 看 RSS 增量、`du -sh` 看 style.db 增长；
5. 观察 3 天无告警后，才允许把 `groups` 扩到主群。

### 17.15 回滚

- 关闭：`enabled=false`（立即停止注入与学习，无需重启）；
- 卸载：删除插件目录 + `plugin_data/astrbot_plugin_dafeiyu_style/`（含 `style.db`）；
- 不影响 `daily_memory` 的任何表（只读、无写入）；
- 不修改 AstrBot 内核、不改 `dafeiyu_runtime`、不新增依赖 → 回滚零风险。

### 17.16 直接粘贴给 GPT 的实现指令

```text
你是一名熟悉 AstrBot（版本 4.29.3，Python 3.11）插件开发的后端工程师。
我要你在 F:\github\BOT用\plugins\ 下新建插件 astrbot_plugin_dafeiyu_style，
严格按 F:\github\BOT用\BOT项目交接手册.md 的 §17 实现，不要自行扩展需求。

硬性约束：
1. 只读 /AstrBot/data/plugin_data/astrbot_plugin_dafeiyu_daily_memory/memory.db 的 events 表
   （uri 只读模式），禁止再采集或保存任何聊天原文；证据字段最多存 120 字。
2. 绝不修改 req.system_prompt；注入只用
   from astrbot.core.agent.message import TextPart
   req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
   挂 @filter.on_llm_request(priority=97)。
3. 所有模型调用必须 await dafeiyu_runtime.paid.guarded_model_call(...)，
   捕获 ModelBudgetBlocked 后本轮跳过，不重试。
4. 不引入任何新 pip 依赖、不建 WebUI、不用 PostgreSQL、不自动改人格。
5. 只消费免费档模型（GLM-4.5-Flash，provider id 来自配置）；模型输出必须纯 JSON，
   解析失败做一次修复，仍失败整批丢弃且不推进游标。
6. 注入内容 ≤5 条规则 + ≤3 对示例、总长 ≤600 字符、超时 1.2s，
   异常一律静默（logger.debug），不得影响正常回复。
7. 插件目录结构与文件职责按 §17.13；SQL 只出现在 store.py。
8. 提供 unittest 覆盖 §17.14 的 6 个场景，并用现有 tests/ 的写法（unittest，不用 pytest）。
9. 代码注释用中文；日志只打数量与状态，禁止打聊天正文。
10. 完成后给出：文件清单、每个文件的职责、如何本地跑测试、如何部署与回滚。

参考现有实现风格：
- source: plugins/astrbot_plugin_dafeiyu_daily_memory/main.py（hook 与 TextPart 用法）
- source: plugins/astrbot_plugin_dafeiyu_group_guard/main.py（配置热读取、管理员命令）
- source: plugins/dafeiyu_runtime/paid.py（预算门）
```

### 17.17 不做的事（避免下一轮返工）

- ❌ 不把 self-learning 的源码拷进本仓库（AGPL 传染；若仅线上运行，需加 `.gitignore` 并保留上游链接）。
- ❌ 不为“情绪/好感度/影子模式/图谱”预留字段或开关（YAGNI，等真有需求再加）。
- ❌ 不让插件自己决定“要不要回复”“回什么”，回复决策永远归主流水线。
- ❌ 不在高峰期（工作日 09–12 / 14–18）跑学习，避免和游戏服抢 CPU。

### 17.18 实现落地记录（v0.1.0，2026-10-30）

> 本节是「已按 §17 写完」的存档，供外部 AI 审查。**审查目标：找 bug 与遗漏，不提新需求。**

**交付文件（全部新增，未改任何现有插件）**

| 文件 | 职责 |
|---|---|
| `plugins/astrbot_plugin_dafeiyu_style/metadata.yaml` | 元信息，格式同现有 8 个插件 |
| `plugins/astrbot_plugin_dafeiyu_style/config.py` | 默认值 + 热读取；类型不符的项忽略；首次启动写 `config.json` |
| `plugins/astrbot_plugin_dafeiyu_style/store.py` | `style.db` 五张表、审批状态机、配额裁剪、30 天清理；SQL 只在本文件 |
| `plugins/astrbot_plugin_dafeiyu_style/prompt.py` | 粗筛/提炼提示词；强制 `evidence` 原文片段 |
| `plugins/astrbot_plugin_dafeiyu_style/learning.py` | 只读 `memory.db` 增量读取 + 两段式 + JSON 解析 + 落库；不依赖 astrbot |
| `plugins/astrbot_plugin_dafeiyu_style/inject.py` | 三类轮流取、总长截断、固定「禁止复述」声明；纯函数 |
| `plugins/astrbot_plugin_dafeiyu_style/main.py` | 注册、`on_llm_request(priority=97)`、预算门、03:00 循环、`/风格` 命令 |
| `tests/test_dafeiyu_style.py` | 12 条用例（unittest） |

**接口契约（外部 AI 审查重点）**

```text
learning.read_events(db_path, since_id, groups, min_len, max_len, limit) -> (rows, max_id)
learning.read_groups(db_path, limit) -> [group_id]
learning.parse_payload(text) -> {rules[<=6], jargon[<=5], few_shots[<=3]} | None
learning.parse_keep(text) -> set[int] | None          # None = 粗筛失败，按全保留处理
learning.process_group(*, db_path, group_id, since_id, cfg, ask, store) -> dict
    ask(system_prompt, user_prompt, stage) -> str      # stage: "coarse" | "refine"，异常向上抛
inject.build_block(rules, jargon, shots, max_chars=600) -> str   # 空/坏行一律返回 ""
store.review(table, row_id, action, actor) -> bool     # 仅 pending 可改
```

**数据流**

```text
daily_memory memory.db(events, 只读)
   └─ read_events(游标增量) → 粗筛(免费模型) → 提炼 → parse_payload
        └─ style.db(pending) → /风格 通过 → approved
             └─ on_llm_request → build_block → TextPart(temp) 注入
```

**关键实现决策**

| 决策 | 原因 |
|---|---|
| `inject_enabled` 默认 `false` | 装上只学不注入，回复零变化；审批后再开 |
| 游标只在 `advanced=true` 推进 | 预算拦截/解析失败/消息不足都不推进，失败不丢数据 |
| `nothing_to_learn` 也推进游标 | 否则同一批消息每天重复调用模型 |
| 注入用 `extra_user_content_parts` + `mark_as_temp()` | 与 daily_memory 完全一致，不动 system_prompt，保前缀缓存 |
| 预算门包住两段调用 | `guarded_model_call` 抛 `ModelBudgetBlocked` 时整轮跳过 |
| 模型调用本地实现 `llm_ask` | 照抄 daily_memory 的 `llm_generate` → `text_chat` 双路径 |
| 三类内容轮流入块 | 避免规则多时把黑话/示例挤掉（曾出现，已修） |
| 表名走 `TABLES` 白名单 | 杜绝命令参数拼 SQL |

**命令**

| 命令 | 说明 |
|---|---|
| `/风格` 或 `/风格 状态` | 开关、待审数、游标、provider、数据源是否在线 |
| `/风格 列表` | 待审规则（当前群） |
| `/风格 黑话` / `/风格 示例` | 待审黑话/示例 |
| `/风格 通过 <id>` / `/风格 拒绝 <id>` | 审批规则 |
| `/风格 黑话 通过 <id>` | 审批黑话（示例同理） |
| `/风格 导出` | 输出 approved JSON（去掉 evidence） |
| `/风格 跑一次` | 管理员手动触发当前群学习 |

**测试（✅ 已通过）**

```bash
python -m unittest discover -s tests -p "test_dafeiyu_style.py"
# Ran 12 tests ... OK   （Python 3.11.7，本机 Windows）
```

覆盖：JSON 四种形态、类型错误降级、粗筛空集、配额裁剪与去重、状态机不可逆、库缺失静默、`too_few` 不推进、解析失败不推进、预算异常不写入、happy path、注入空值与 300/600 字符截断。

**已知未验证点 / 请审查（🟡 真实运行时才会暴露）**

1. `StarTools.get_data_dir("dafeiyu_style")` 是否自动带 `astrbot_plugin_` 前缀未实测；已用「双目录回退」兼容 `astrbot_plugin_dafeiyu_daily_memory` 与 `dafeiyu_daily_memory` 两种命名。
2. `event.get_group_id()` 在 qq_official 适配器下可能返回 openid 或空 → 注入会静默失效（学习走 `events.group_id`，不受影响）。
3. `datetime.now()` 用容器本地时区判断「03 点」，若容器时区不是 Asia/Shanghai 会偏移。
4. `Store` 是单 sqlite 连接（`check_same_thread=False`），后台学习与注入 hook 并发时有 `database is locked` 可能；已设 `timeout=10`，未做重试。
5. `parse_payload` 只剥离 ```json 包裹与截取最外层 `{}`，**不做截断修复**；模型输出被 max_tokens 截断时整批丢弃（预期行为，但请确认可接受）。
6. 未写 `_conf_schema.json`（与 group_guard 一致，靠手改 `config.json`）；面板不会出现可视化配置项。
7. `guarded_model_call` 是**全局额度**，不区分群；单群学习可能被全局预算先拦。
8. 私聊 `group_id` 为空 → 列表/注入均不生效（设计如此）。
9. 审批状态机：`review` 只允许 pending → approved/rejected，已处理记录无法回滚（如需回滚要手动改库）。
10. `config.json` 由 `_ensure_config` 首次启动写入，写入发生在 `load_config` 之后 → 首次运行用内置默认值，第二次起读文件，行为一致但请确认时序。

**请外部 AI 只回答三类问题**：① 上述接口契约与实现是否一致；② 10 个未验证点里哪些会真出问题、怎么改；③ 有没有违反 §17.4「非目标」的地方。**不需要新增功能建议。**

---

## 变更记录

- **2026-10-23**：初版。架构、访问方式、关键文件、已做改动、红线、常用命令。
- **2026-10-24**：SnowLuma 双实例澄清；主群主动消息打通；晨报改 8 点；FAQ 补关键词；
  告警去重持久化；插件代码纳入本机 git + 每日 04:00 自动快照。
- **2026-10-26（本版，大幅重写）**：
  1. **移除明文 SSH 口令**（旧版把口令写在文档里，而本文要贴进第三方 AI 对话）。
  2. 补全**模型分工表**与**成本结构实测**（system/tools/extra 五维拆解）。
  3. 记录**两种请求形态 A/B** 与框架根因，并澄清 `cache_events` 的误报机制。
  4. **语音三层降级**上线，含 fake-ip 导致的 TLS 断连问题与 SSH 隧道解法。
  5. Skills 注入治理（−632 token/请求）、人格精简 + 加「对 TOKI 的占有欲」、
     记忆召回改为按相关性（`recent_memory_count=0`）。
  6. 依赖持久化（`pylibs` + 阿里云 pip 镜像）、框架 stats bug 补丁挂 `post_start`。
  7. 补充编码类坑（BOM / GBK ×2 / ASCII 文件名 / markdown 不进人格 prompt）。
  8. 修正服务器规格（2GB → 3.5GB）与预算（日 1 元 → ¥0.45）。
- **2026-10-28（本版，新增 §13）**：
  1. **缓存专项改造**：`max_turns 8 → -1`（关滑窗）、关内置 `web_search`、anysearch 限流 5、
     livingmemory `top_k 6 → 3` → **命中 25~65% → 90~92%**。
  2. **装 CacheGuard 1.3.0**（wire 级最终请求分段对比）+ 打补丁把「token 预测」与「裁剪」解耦（误差 0.1~4.6%）。
  3. **自建真版 `/ctxbudget`**（说明书插件 1.5.0）：管理员看真实读数、非管理员静默、未知命令也静默，**全部 0 token**。
  4. **proactive_chat 超限前置跳过补丁**（该插件直连 `text_chat()` 绕过预算钩子）。
  5. **修 `admins_id` 格式错配**（框架比对 QQ 号，面板填的是 openid → 全员不是管理员）。
  6. 记录 7 个今天新踩的坑（混入类 handler 不被调度、reload-all 重置工具顺序、
     plugins 目录留备份目录被当插件、qq 的 At 是方括号格式等）→ 见 §13.5。
  7. 预算口径改为日 200 万 token + ¥1.0；**遗留 4 项风险**（见 §13.6）。
- **2026-10-29（本版，新增 §14 + 确定性标注约定）**：
  1. **引入确定性标注约定**（✅已验证 / 🟡推断 / ❓未验证 / ⛔已作废），全文按此标注。
  2. **新增 §14 复核**：15 条已验证结论 + 9 项不确定/待处理 + 3 条只做了推断的结论 + 可复现的确认命令。
  3. **确认缓存改造效果持续**：10-29 全天命中 84.1%，最近单次 85~95%；重启后首次请求 10.8% 属正常重建。
  4. **确认内置 `web_search` 全天 0 次调用**、anysearch 仅 3 次且限流生效、livingmemory `top_k=3` 生效（extra 3038→1700）。
  5. **记录 §14.2-1 最重要的未解决问题**：`/ctxbudget` 被框架命令处理器接管后，
     **管理员发也静默**（20+ 次调用零回复、零 LLM 请求）→ 管理员判定 `_is_admin()` 失效，原因未定位。
  6. 确认 handler 归属问题已彻底解决（`dg_ctxbudget_*@main`，无 `@ctxbudget` 残留）。
  7. 确认 llm_compress **至今一次未触发**（messages 未过 52.5k 阈值），MiMo 作为压缩 provider 的风险**未验证**。
  8. 预算更新为日 300 万 token（用户 10-29 从 200 万上调）。
  9. 澄清 dashboard `anyio.EndOfStream` 是客户端断连噪声，非故障。
- **2026-10-29 傍晚（本版，新增 §4.4 + §15）**：
  1. **成本口径改以官方账单为准**：面板低估 11 倍（10-29 官方 ¥0.6949 / 面板 ¥0.4963），
     根因是 `price_selections` 候选价覆盖 + 内核按 agent run 记账；两处均已修。
  2. **新增对账脚本** `scripts/cost/cost_reconcile.py`（`--save` 累积系数），确定口径
     「账单 ≈ 本地记录 × 1.52」。
  3. **作废「知识库整份注入」判断**：线上 KB 仅 21 个分块、单次 ≤1.5k 字；`knowledge.md` 无人读取。
  4. **记忆**：`min_importance_for_retrieval` 0.12 → 0.3；确认记忆跨群共享（122 条同池），
     但严格比对近 24h **无串群证据**。
  5. **分群**：`daily_memory` v0.3.1 注入「当前会话」标签 + 群别名 `session_labels`（两个群实测都答对）。
  6. **磁盘**：清理 3.7G（停用 snowluma 镜像/容器 + temp + nginx 归档），74% → 65%；
     新增 `clean-temp.sh` 每日 04:10 cron。
  7. **新增 `dafeiyu_group_guard` v1.0.0（§16）**：群聊每日 ¥0.5（账单估算，按群独立）+ 工作日
     09–12/14–18 高峰休眠；私聊完全放开；休眠/超额期消息**不补答**；配置热读取（改配置免重启）。
- **2026-10-30（本版，新增 §17）**：
  1. **评估第三方 `self-learning` 4.4.4 并决定不启用**：与 `daily_memory` 双写/双重注入、
     后台学习绕过 `group_guard`、成本不在账本、AGPL 传染，均记录在 §17.2。
  2. **智谱结论**：可用但只限免费档（GLM-4.5-Flash）；付费档会脱离现有账本（`allowed_hosts` /
     计价 / 余额查询均写死 DeepSeek，见 §17.2 成本缺口）。
  3. **资源评估**：开启后估算 +80–200MB（服务器仅 3.5GB 且与游戏服同机），故不在线上启用。
  4. **新增自建规格 `astrbot_plugin_dafeiyu_style`**：只读 `daily_memory` 的 `events`，
     只做风格规则/黑话/few-shot，注入用临时 part、走 `guarded_model_call`、目标 RSS <30MB。
  5. **§17.16 提供可直接粘贴给外部 AI 的实现指令**，含硬性约束、目录结构、测试与验收/回滚。
  6. **已实现 `astrbot_plugin_dafeiyu_style` v0.1.0（§17.18）**：新增 8 个文件 + 12 条单测（本机
     Python 3.11 全通过）；`inject_enabled` 默认关，先只学不注入；未在真实 AstrBot 运行时验证，
     未验证点与审查清单见 §17.18。

## 2026-10-26 内置知识库内容更新

已向 AstrBot 现有知识库“卫戍协议：盟约”（593b2baa-4c29-44e8-8421-1a8e3f97a50f）导入12份文档，12个分块，导入失败0。使用现有 zhipu/embedding-3 建索引；现有配置 kb_names 已选择该库，未更换模型或预算。

本地维护源：F:\github\BOT用\知识库\卫戍协议。覆盖项目边界、服务器入口、新手、素材省流、皮肤、每日累计积分、永久成就与身份、房间限制、断线恢复、故障反馈、近期更新和机器人回答边界。

已测试累计积分、重启恢复、皮肤名称三项检索，均命中对应文档。未实际发群消息验证聊天回答。

线上运营插件 faq.json 同步纠正旧的10人满员、固定房间限制、服务器完整素材下载、重启保证恢复等说法。修改前备份：faq.before-kb-20261026.json；同步后的本地备份位于知识库源目录。没有重启机器人或游戏服务器。

知识核对日为2026-10-26，维护时间与已知故障有时效，后续修复须更新对应文档。不要反复导入 import.json 造成重复文档，应先检查已有文档并使用更新流程。人数、开服状态、当天榜单必须实时查询，知识库不是监控数据。

## 2026-10-26 知识检索与成本诊断优化
- 现有内置知识库为 agentic 按需检索，不是12篇全文常驻。运营助手 query_project_info 优先查现有内置库，失败才回落 FAQ，移除“查不到就让玩家换游戏”的旧回答。
- FAQ 参考改放当轮用户输入，保持人格系统前缀；已附FAQ时移除重复知识检索工具（保留未完成工具调用），不改变人格或长期记忆。
- 核对报Bug、状态等规则回复已 stop_event；总结网页/识图等显式模型请求仍保留原有执行流程，不能一概拦截。
- 缓存比较按会话、provider/model、conversation、工具请求形态分开；单条上下文更新不再记作 order_drift。最近历史样本是单条内容变化，不能据此证明乱序或全部缓存失效。仅保存角色/长度等结构证据，不新增聊天原文存储。
- 成本总览和预算页增加7个完整自然日估算（月Token/费用分别展示）。0消耗不报无限天；超限、未设上限独立显示。月预算重置后重算；不等同供应商余额。统计不覆盖绕过此插件的外部调用。
- 预算与模型未改：当前线上日750000token/0.45元、月5000000token/22元、stop，按实时API为准。
- 64项回归检查、FAQ注入检查、TypeScript与面板构建通过。只重载运营助手与成本插件，没有重启容器/游戏；未发送测试群消息。缓存实际节省需要后续真实请求评估。
- 本次源码：F:\github\BOT用\review\cost-kb-20261026；上线前备份：/opt/bot-stack/astrbot/data/backups/cost-kb-20261026-203513。
