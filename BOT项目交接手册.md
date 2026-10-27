# BOT 项目交接手册（大肥鱼 + 卫戍协议同人游戏服）

> **用途**：给任何一个**没有历史上下文**的 AI 助手 / 协作者，在 3 分钟内建立对这套系统的正确认知，
> 知道「文件在哪、怎么连、哪些事不能乱动、哪些坑已经踩过」。
>
> **本版时间**：2026-10-26 19:50（**在旧版基础上大幅重写**，旧版停于 10-23，大量信息已过期）
> **接手方式**：把本文 §12「30 秒摘要」整段贴给新对话当背景，需要细节时再让它读对应章节。

---

## 0. 一句话画像 + 当前状态快照

**大肥鱼**是一个跑在 **AstrBot** 上的 QQ 群机器人，服务《**卫戍协议：盟约**》同人游戏社群。
机器人与游戏服跑在**同一台阿里云轻量服务器**上。非官方同人作品，免费、严禁盈利。

```
服务器    阿里云 SWAS 轻量，2核 / 3495MB 内存 / 无 GPU / 磁盘 40G（已用 70%）
域名      game.lingluotoki.dpdns.org  →  101.132.104.41
机器人    Docker 容器 astrbot（soulter/astrbot:pinned-4.29.3），端口 6185(WebUI)
游戏服    systemd stronghold(3000) + stronghold-3001(3001)，均 active
今日用量  94 请求 / ¥0.334 / 日限 ¥0.45 / 缓存命中 60.3%
预算      日 750,000 token + ¥0.45；月 5,000,000 token + ¥22；超限行为 = stop（直接停回复）
```

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
    ├─ stronghold-3001.service :3001  联盟服 4人
    ├─ stronghold-control      observe.py 内存监控
    └─ stronghold-bot-proxy    bot_proxy.py 只读状态桥
```

| 组件 | 形态 | 端口 | 备注 |
|---|---|---|---|
| AstrBot | Docker `astrbot` | 6185 WebUI / 6199 | 镜像 `soulter/astrbot:pinned-4.29.3`（**pin 死，别随便升级**） |
| 游戏主服 | systemd `stronghold` | 3000 | 入口 `/root/stronghold/server/net.js` |
| 游戏联盟服 | systemd `stronghold-3001` | 3001 | MOD 定制 |
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

### 3.2 已装插件（15 个，全部启用）

```
astrbot_plugin_wushu_ops      自研：FAQ 知识库、服务器状态、项目问答工具
spectrecore                   v2.1.13 人格对话核心
astrbot_plugin_cost_control   0.4.1 成本/预算/缓存诊断（attribution 极有用）
astrbot_plugin_usage_view     1.1.0 群里发「用量」查 token
astrbot_plugin_livingmemory   长期记忆（每日日记 + 向量召回）
astrbot_plugin_clonetts       TTS（本机 GPT-SoVITS → MiMo → edge 三层）
astrbot_plugin_kaomoji        颜文字（依赖人格里的 {{分类}} 标记）
meme_manager                  表情包（含情感模型选图）
astrbot_plugin_group_guardian 群管（审核/撤回/禁言，大肥鱼是管理员）
astrbot_plugin_listen_music   点歌
astrbot_plugin_anysearch      联网搜索/网页正文（48h 真实调用 0 次，但保留）
astrbot_plugin_bili_dynamic   B 站动态
astrbot_plugin_parser         消息解析
astrbot_plugin_proactive_chat 主动消息（间隔 3~16h，0-8 点静默）
astrbot_plugin_cacheprobe     ⚠️ 我加的临时探针，诊断完应删除（见 §10）
```

### 3.3 宿主机关键路径

| 路径 | 作用 |
|---|---|
| `/opt/bot-stack/docker-compose.yml` | 容器编排（含 `post_start` 补丁 + `PIP_INDEX_URL` 阿里云镜像） |
| `/opt/bot-stack/bin/local_tts_server.py` | 本地语音代理（纯标准库） |
| `/opt/bot-stack/bin/local_tts_token.txt` | 代理访问令牌（600，勿外传） |
| `/opt/bot-stack/bin/deps_health_notify.py` | 依赖体检 + 配置守卫（cron 每 30 分钟） |
| `/opt/bot-stack/logs/` | `deps-health.log` / `local-tts.log` / 用量汇总 |
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

---

## 5. 缓存与「两种请求形态」（此前的最大谜团，已基本查清）

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
| 1 | **三个 API Key 轮换** | **高** | DeepSeek / MiMo / 智谱的 Key 曾完整暴露在对话记录里。改完要重启容器（provider 配置变更）。 |
| 2 | **删除 `astrbot_plugin_cacheprobe`** | 中 | 我加的临时探针，每请求多一行日志。用途是抓「形态 A（全量工具）」的调用方——**抓完就删**。 |
| 3 | 形态 A 的调用方定位 | 中 | 约 1200 token/请求。等群里有消息时看探针的 `caller=` 字段。 |
| 4 | 人格 `skills`/`tools` 是否还要精简 | 低 | `anysearch_*` 两个工具 48h 真实调用 0 次，但占 ~160 token；优先级低于 #3。 |
| 5 | snowluma 容器要不要恢复 | 低 | 当前只有 astrbot 在跑，机器人走 qq_official 正常。**拉起前先确认不会和官方平台重复接管消息。** |
| 6 | 参考音频只有 3.02 秒 | 低 | 在 GPT-SoVITS 建议下限（3~10s）。若音色不稳，换 5~10 秒干声会明显改善。 |
| 7 | 知识库 `kb.db` 仍空 | 低 | AstrBot 内置 RAG 从未接入文档；当前 `faq.json` 关键词直答已够用。 |
| 8 | 游戏 AI 队友「变肥鱼」 | 最低 | 游戏插件系统禁止改服务端逻辑，只能动 `lobby.js`/`botEmotes.js`，须低峰重启游戏服。 |

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
> **预算**：日 75 万 token + ¥0.45，超限直接停回复（`on_exceeded=stop`），次日 0 点刷新。
>
> **语音**：三层降级——本机 GPT-SoVITS v2Pro（洛天依 13 周年模型，RTX 4060）→ MiMo voiceclone → edge-tts。
> 服务器起 `local-tts` 代理（32000），本机客户端**走 SSH 隧道** `http://127.0.0.1:32000` 连它
> （因为这台机器直连该域名会被 fake-ip 代理规则打断 TLS）。
>
> **知识库**：`/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/faq.json`（现读生效）。
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
