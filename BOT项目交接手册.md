# BOT 项目交接手册（大肥鱼 + 卫戍协议同人游戏服）

> **用途**：给任何一个**没有历史上下文**的 AI 助手 / 协作者，在 3 分钟内建立对这套系统的正确认知，
> 知道「文件在哪、怎么连、哪些事不能乱动、哪些坑已经踩过」。
>
> **本版时间**：2026-10-28 19:10（**在 10-26 版基础上补入 10-27/10-28 两天的缓存专项改造**）
> **接手方式**：把本文 §12「30 秒摘要」整段贴给新对话当背景，需要细节时再让它读对应章节。
> **如果只关心「为什么今天改成这样」**，直接看 **§13「2026-10-28 缓存专项改造纪要」**。

---

## 0. 一句话画像 + 当前状态快照

**大肥鱼**是一个跑在 **AstrBot** 上的 QQ 群机器人，服务《**卫戍协议：盟约**》同人游戏社群。
机器人与游戏服跑在**同一台阿里云轻量服务器**上。非官方同人作品，免费、严禁盈利。

```
服务器    阿里云 SWAS 轻量，2核 / 3495MB 内存 / 无 GPU / 磁盘 40G（已用 70%）
域名      game.lingluotoki.dpdns.org  →  101.132.104.41
机器人    Docker 容器 astrbot（soulter/astrbot:pinned-4.29.3），端口 6185(WebUI)
游戏服    systemd stronghold(3000) + stronghold-3001(3001)，均 active
今日用量  151 请求 / 177.6 万 token / ¥0.88 / 日限 ¥1.0
缓存命中  全天累计 73.8%；**改造后单轮稳定 90~92%**（改造前只有 25~65%）
预算      日 2,000,000 token + ¥1.0；月 5,000 万 token + ¥10；超限行为 = stop（直接停回复）
```

> ⚠️ 上面「全天累计 73.8%」被白天两次事故拉低（搜索风暴 + 滑窗重算）。
> **判断现在好不好，看最近 5 次请求即可**，别看累计值。

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

### 3.2 已装插件（2026-10-28 实测 24 个）

```
astrbot_plugin_wushu_ops      自研：FAQ 知识库、服务器状态、项目问答工具
astrbot_plugin_cost_control   0.4.1 成本/预算/缓存诊断（attribution 极有用）
astrbot_plugin_usage_view     1.1.0 群里发「用量」查 token
astrbot_plugin_livingmemory   长期记忆（语义召回；top_k=3，注入走 extra_user_content）
astrbot_plugin_dafeiyu_help   说明书 1.5.0：/功能 + 真版 /ctxbudget（管理员诊断，0 token）
astrbot_plugin_dafeiyu_daily_memory  情景记忆：原话留档 + 人物画像 + 事实（自研）
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
| 3 | ~~形态 A 的调用方定位~~ | 中 | **已定位并修复（2026-10-28）**：根因是 `agent_runner.config.persona` 缺 `tools`/`skills`，框架回落到 `get_full_tool_set()` 导致同会话 20↔13 工具交替；已按 persona 12 补齐，备份 `cmd_config.json.bak-persona-tools-20261028-185047`。 |
| 4 | 人格 `skills`/`tools` 是否还要精简 | 低 | `anysearch_*` 两个工具 48h 真实调用 0 次，但占 ~160 token；优先级低于 #3。 |
| 5 | snowluma 容器要不要恢复 | 低 | 当前只有 astrbot 在跑，机器人走 qq_official 正常。**拉起前先确认不会和官方平台重复接管消息。** |
| 6 | 参考音频只有 3.02 秒 | 低 | 在 GPT-SoVITS 建议下限（3~10s）。若音色不稳，换 5~10 秒干声会明显改善。 |
| 7 | 知识库 `kb.db` 仍空 | 低 | AstrBot 内置 RAG 从未接入文档；当前 `faq.json` 关键词直答已够用。 |
| 8 | 游戏 AI 队友「变肥鱼」 | 最低 | 游戏插件系统禁止改服务端逻辑，只能动 `lobby.js`/`botEmotes.js`，须低峰重启游戏服。 |
| 9 | **实测 `/ctxbudget`** | **高** | 新写的真版诊断还没在群里跑通一次（曾因混入类 handler 归属问题失效，已改名 `dg_ctxbudget_*` 并重启）。管理员发一次应看到真实数据；非管理员发应**完全静默**。 |
| 10 | **金额闸门 ¥1.0 与 token 闸门口径不一致** | **高** | 18:00 触顶后机器人静默装死 6 次且无提示。二选一：金额上限抬到 ¥1.5；或关掉金额闸门只留 token 闸门（token 口径准）。见 §13.6-4。 |
| 11 | **确认群管插件是否有意禁用** | 中 | `astrbot_plugin_group_guardian` 禁用中 → 撤回/禁言/踢人/群公告全部失效。见 §13.6-3。 |
| 12 | `compression.provider_id` 改成 MiMo 的风险 | 中 | MiMo 窗口小，52k 触发的压缩请求可能失败 → 退回滑窗、命中掉回去。见 §13.6-1。 |

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
> **预算**：日 200 万 token + ¥1.0，超限直接停回复（`on_exceeded=stop`），次日 0 点刷新。
>
> **缓存（10-28 改造后）**：`compression.max_turns = -1` 关掉了滑动窗口——滑窗与前缀缓存天生相斥，
> 丢最老一轮就会让历史起点改写、20k 历史每 1~2 轮重算。system/tools 段钉死不动，
> 记忆/时间戳只塞最后一条消息。命中率已从 25~65% 提到 **90~92%**。
> 内置 `web_search` 已关（¥0.005/次）、anysearch 限流 5 条、livingmemory `top_k=3`。
> 诊断用 CacheGuard 1.3.0 的 `/缓存状态`、`/缓存断点`（wire 级分段对比）。详见 §13。
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
      "overflow_strategy": "llm_compress",// 到 64k×0.82≈52.5k 自动 LLM 摘要
      "keep_recent_ratio": 0.25,
      "provider_id": "xiaomi/mimo-v2.6-flash",   // ← 用户后来改成了 MiMo，见 §13.6 风险
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
// plugin_data/astrbot_plugin_cost_control/config.json
"budgets":      { "per_session_daily": 2000000, "global_daily": 2000000, "global_monthly": 50000000 }
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

### 13.6 遗留问题 / 风险（接手后优先看这里）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | **`compression.provider_id` 已改成 MiMo** | MiMo 的上下文窗口远小于 52k，**压缩请求本身可能超限失败**；失败会退回 `truncate_by_turns`（滑窗回来，命中又掉） | 触发压缩时盯日志；若失败率高，改回 `deepseek/deepseek-flash` |
| 2 | `/ctxbudget` 曾有"旧 handler 残留"（注册在 `ctxbudget` 子模块） | 会与新 handler 抢同一条命令、抛属性异常 | 已把三个 handler 改名 `dg_ctxbudget_*` 并重启；**用 `/ctxbudget` 实测一次确认** |
| 3 | **群管插件 `astrbot_plugin_group_guardian` 处于禁用状态** | 撤回 / 禁言 / 踢人 / 群公告等 20 多项功能**全部失效**（大肥鱼本来是群管理员，是会真执行的） | 确认是否有意禁用；要用就恢复（注意它会带 20 多个工具 schema 回工具段） |
| 4 | 金额闸门 ¥1.0 与 token 闸门 200 万**口径不一致** | 18:00 就触顶，机器人**静默装死** 6 次（群里表现为"怎么不回你了"），且**拦截时不发任何提示** | 二选一：金额上限提到 ¥1.5；或关掉金额闸门只留 token（token 口径准）。另外给拦截加一句提示 |
| 5 | `fallback_max_tokens=16000` 是死配置 | 误导人 | 改成 64000（纯文档性，不影响行为） |
| 6 | 生日记忆串号 | 大肥鱼被记成"10月25日"（那是耀白的生日） | 情景记忆抽取规则待修（引用消息主语归属） |
| 7 | `datetime_system_prompt=false` | 模型失去"当前时间"感知 | 若需要时间，改由插件在**消息末尾**注入（不影响前缀） |

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
