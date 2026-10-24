# BOT 项目交接手册（大肥鱼 + 卫戍协议同人游戏服）

> 用途：给任意接手本项目的 AI Agent / 协作者，在**不翻阅历史对话**的情况下，30 秒内建立对这套系统的正确认知，并知道"文件在哪、怎么连、哪些事不能乱动"。
> 维护时间线：截至 2026-10-23，由 WorkBuddy 代维护。后续每次大改动请同步更新本文件末尾的「变更记录」。

---

## 0. 一句话项目画像

- **大肥鱼**：一个跑在 **AstrBot** 上的 QQ 群机器人（人格化、零 token 直答 FAQ、服务器状态播报），服务于《**卫戍协议：盟约**》同人游戏社群。
- **同机**：机器人和游戏服务器**在同一台 2GB 内存的阿里云轻量服务器**上，两者通过宿主机只读 API 桥接（非容器互通）。
- **性质**：非官方同人作品，完全免费、严禁盈利，与鹰角 / Yostar 无关。
- **部署**：已上线，公网域名 `game.lingluotoki.dpdns.org`（解析 → `101.132.104.41`）。

---

## 1. ⚠️ 主机访问方式（最重要）

| 项 | 值 |
|---|---|
| 机器 | 阿里云 SWAS 轻量应用服务器，上海区，2 核 / **2GB 内存 / 无 GPU** |
| 公网域名 | `game.lingluotoki.dpdns.org`（→ 101.132.104.41） |
| 实例内网 IP | `172.24.30.154` |
| 实例 ID | `c1a38b807dc64d24ba87320cb03c11e4`（ResourceInstanceId `i-uf6b6qi46umz858xp59y`） |
| SSH 登录用户 | **`root`**（admin 的 `authorized_keys` 没加 robot 公钥，只用 root） |
| SSH 私钥 | `F:\github\BOT用\robot.pem`（RSA，口令见 `F:\github\BOT用\密码.md`，口令 `Chen051107*`） |
| 去口令副本 | `F:\github\BOT用\.robot-nopass.pem`（本会话生成，敏感，**用完即删、勿提交、勿交 LLM**） |

**连接命令模板（在本机 Git Bash 跑）：**
```bash
ssh -i "/f/github/BOT用/.robot-nopass.pem" -o ConnectTimeout=60 -o ServerAliveInterval=30 -o StrictHostKeyChecking=no root@game.lingluotoki.dpdns.org "命令"
```
> 若 `.robot-nopass.pem` 已删：用 `openssl rsa -in robot.pem -out .robot-nopass.pem -passin pass:Chen051107*` 重新派生（口令来自 `密码.md`）。

**🚫 红线：2GB 小内存机器，不要随意重启系统或游戏服。** 重启会让页缓存清空 + swap 颠簸，导致 SSH/服务一阵极慢甚至起不来（已踩过坑）。非必要不 reboot；重启游戏服 = 中断正在进行的对局。

---

## 2. 系统架构（线上）

```
浏览器/QQ → 公网 game.lingluotoki.dpdns.org
                  │
            nginx (:80 / :443 HTTPS 反代)
                  ├── 卫戍协议游戏主服   systemd: stronghold.service      (:3000, 6人, 官方内容)
                  ├── 卫戍协议联盟服     systemd: stronghold-3001.service (:3001, 4人, MOD定制)
                  └── 游戏服只读 API    /opt/stronghold-stability/control.py (127.0.0.1:31998, 仅 localhost+admin)
                          │（复用 SP_ADMIN_TOKEN，只读 GET /stability）
                          ▼
                  bot_proxy.py（stronghold-bot-proxy, 172.18.0.1:31999, docker 私有桥只读）
                          ▲
            astrbot 容器（soulter/astrbot, :6185 WebUI / :6199 网关）── 大肥鱼本体
                          ├── 平台适配器：qq_official(官方机器人) + aiocqhttp(snowluma 个人号)
                          └── 插件：wushu_ops(自研) / spectrecore(人格对话) / cost_control(预算)
```

| 组件 | 形态 | 端口 | 说明 |
|---|---|---|---|
| AstrBot | Docker 容器 `soulter/astrbot` | 6185(WebUI) / 6199 | 大肥鱼机器人本体，配置在容器内 `/opt/bot-stack/astrbot/data/` |
| 游戏主服 | systemd `stronghold.service` | 3000 | 官方内容·6人， admitted via admission.mjs |
| 游戏联盟服 | systemd `stronghold-3001.service` | 3001 | MOD 定制·4人 |
| stronghold-stability | 宿主机 Python/Node 套件 `/opt/stronghold-stability/` | — | 内存监控 `observe.py` + 准入闸 `admission.mjs` + 控制 API `control.py` |
| stronghold-bot-proxy | systemd `stronghold-bot-proxy` | 172.18.0.1:31999 | **只读**桥，给容器内机器人查宿主机真实状态（不暴露写操作） |
| snowluma（本地） | **本机 Windows 程序** `snowluma相关/runtime-v1.14.22/` | 5099(WebUI) | **实际在用的 QQ 个人号客户端**，带本机 GPU，日志在 `logs/snowluma-YYYY-MM-DD.log`。面板 06「个人提醒」内新增「本地 SnowLuma 日志」子块直接读它（不经过服务器） |
| snowluma（服务器容器） | Docker 容器（个人号 OneBot） | — | **当前 Exited(0)**（12:38 GPU 崩溃）；面板「运行日志→SnowLuma」页拉的是它的日志。**⚠️ 待确认：此容器是否仍在使用 / 还是遗留部署**——本机 Windows 实例才是活着的那个 |

**关键隔离事实**：astrbot 容器与宿主机 systemd 服务**隔离**——容器看不到宿主机进程，也不能用 systemctl。机器人查服务器状态靠 `bot_proxy.py` 只读 HTTP 桥，而**不是**给容器 shell 权限（见红线）。

**SnowLuma 部署澄清（2026-10-23 修正）**：SnowLuma **不是只在服务器上**。本机有一份 Windows 原生运行实例（`snowluma相关/runtime-v1.14.22/`），一直在跑、带 GPU、是真正挂 QQ 个人号的那个；服务器上另有一个 snowluma 容器（12:38 崩了、无 GPU）。面板里「运行日志→SnowLuma」拉的是服务器容器日志（已死），「个人提醒→本地 SnowLuma 日志」拉的是本机实例日志（活的）。**服务器容器是否还需恢复，待用户确认**（疑似遗留）。

---

## 3. 关键文件位置（线上，改前先 cp 备份）

备份命名习惯：`原文件名.bak-YYYYMMDD-HHMM`（已有一堆，回滚时 cp 回去再 restart 对应服务即可）。

### 3.1 机器人（astrbot 容器内 `/opt/bot-stack/astrbot/data/`）
| 文件 | 作用 | 改后生效方式 |
|---|---|---|
| `plugins/astrbot_plugin_wushu_ops/faq.json` | **真正的"知识库"**：零 token 直答，`entries` 列表，每项 `{id,priority,keywords,question,answer}` | **每次提问现读文件，改完无需重启 astrbot** |
| `plugins/astrbot_plugin_wushu_ops/main.py` | wushu_ops 插件主体：`/服务器状态`、`/运营帮助`、监控播报、`get_server_status`/`query_project_info` LLM 工具、`_load_faq()` 现读 | 改代码需**重启 astrbot** |
| `plugin_data/astrbot_plugin_cost_control/config.json` | 成本预算护栏：`budgets_cost`(¥ 日1/月10 CNY)、token 预算、`default_on_exceeded=warn`、每日 09:00 推送 TOKI | 改后需**重启 astrbot**（但本项目刻意推迟重启以免 context_reset） |
| `config/spectrecore_config.json` | spectrecore 插件配置，`group_msg_history`(历史滑动窗口条数，当前 6) | 重启 astrbot |
| `cmd_config.json` | **AstrBot 主配置**（注意：不在 `config/` 下，曾有脚本路径坑） | 重启 astrbot |
| `knowledge_base/kb.db` | astrbot 内置 RAG 向量库（SQLite+WAL） | **三张表全是 0 行，从未接入文档**，裸塞 SQL 检索不到，需先配 embedding provider 再走文档上传 |

### 3.2 游戏服与稳定性（宿主机 `/opt/stronghold-stability/` 与 `/root/stronghold*/`）
| 文件 | 作用 | 改后生效方式 |
|---|---|---|
| `/opt/stronghold-stability/start.mjs` | 游戏服入口，`installAdmission(...)` 调用点；准入闸参数在此传 | **必须重启游戏服**（stronghold + stronghold-3001）才会生效，会踢人 |
| `/opt/stronghold-stability/admission.mjs` | 准入闸逻辑（库默认 minAvailableMB=128/resumeAvailableMB=224/maxRssMB=700，但实际用 start.mjs 传值覆盖） | 同上 |
| `/opt/stronghold-stability/observe.py` | 内存监控脚本，第 26 行 `availableMB < 400` 即弹"低内存"告警（原 192） | 重启 stronghold-control 生效 |
| `/etc/sysctl.conf` | `vm.swappiness=10`（修过覆盖雷；原本写 0 把 stronghold 自带 90/99 配置覆盖成 0，导致几乎不换页易 OOM） | `sysctl --system` |
| `/etc/fstab` + `/var/swap-stronghold.img` | 2GB swap 文件（共 4GB swap），开机自挂 | 已生效 |
| `/root/stronghold/server/lobby.js` | 游戏 AI 队友名硬编码 `BOT_NAMES`（华法琳/阿米娅/惊蛰/杜宾/凯尔希/可露希尔） | 改需重启游戏服 |
| `/root/stronghold/server/match/botEmotes.js` | AI 队友只发固定表情 id，无自由文本 | 改需重启游戏服 |

---

## 4. 机器人能力地图（大肥鱼能做什么）

- **自动答疑（零 token 直答）**：群友直接问（不用@）关键词命中 `faq.json`，如"地址/浏览器/素材/主屏幕/卡顿/免费/游玩指引/满员"。这是群友感知最强的功能。
- **`/服务器状态`**：返回双链路（公网 healthz + 宿主机进程级）实时状态——各服在线/版本/房间/对局、主机内存剩余/交换已用/负载/告警。
- **监控播报**：定时检测异常（准入未激活、数据过期、可用内存<200MB OOM 预警、版本变更、重启）并推送。**注：主动推送依赖 snowluma 个人号——本机 Windows 实例是活着的，但推送是否畅通待确认（服务器 snowluma 容器已死，疑似遗留）。**
- **@聊天 / 点歌 / 查资料**：人格对话（spectrecore）+ 音乐/搜索工具。
- **`/运营帮助`**：面向群友的使用指南（报 bug 示例、自动答疑清单、@聊天/点歌/查资料）。
- **LLM 工具**：`get_server_status`、`query_project_info`（供人格调用，不暴露写权限）。

---

## 5. 本地文件清单（`F:\github\BOT用\`）

| 路径 | 用途 |
|---|---|
| `robot.pem` / `.robot-nopass.pem` | SSH 私钥（见 §1，敏感） |
| `密码.md` | 含 SSH 口令 `Chen051107*`，**切勿外传/提交** |
| `harden-memory.sh` + `harden-memory-说明.md` | 内存加固脚本与说明（加 swap / swappiness / 阈值，幂等） |
| `astrbot相关/` | AstrBot 官方 Launcher 安装包（Windows） |
| `知识库/` | 本地素材知识库（含「鸣潮」子目录，非线上 faq） |
| `云端部署提示词/` | `云端部署.docx` + `云端部署流程一览.html`（部署参考） |
| `人格提示词及表情包/` | 人格/表情包素材（`大肥鱼群友`、`wuwa-aemeath`、`使用说明`、表情插件用法.docx） |
| `管理面板/` | 本地 BOT CONSOLE 服务器管理面板（panel_server.py / index.html / README.md），注意其命令后端曾 502 不可达 |
| `snowluma相关/` | SnowLuma 个人号客户端压缩包 + 使用说明 |
| `dsh桌面端/` | DSH 桌面端安装包（官方/非官方） |
| `TTS素材/` | TTS 语音素材（爱弥斯） |
| `其他环境依赖/` | NapCatQQ 整合包、VC_redist、node 安装包 |

---

## 6. 截至 2026-10-23 已完成的运维改动

| 项 | 改动 | 状态 |
|---|---|---|
| 内存 OOM 兜底 | +2GB swap（共 4GB）+ `/etc/sysctl.conf` swappiness `0→10`（修了覆盖雷） | ✅ 持久生效 |
| 低内存告警 | `observe.py` 阈值 `192→400MB` | ✅ 重启 stronghold-control 生效 |
| 准入闸放宽 | `start.mjs`：主服 maxRssMB `700→900`、两服显式 `minAvailableMB=192`（3001 保留 500） | ✅ 重启游戏服生效（备份 `start.mjs.bak-adm-20261023-1707`） |
| 成本护栏 | cost_control 加 **¥** 预算（日1/月10 CNY）+ `default_on_exceeded=warn` | ✅ 写盘，下次自然重启生效 |
| 缓存去噪 | `cache_hit_rate_alert_threshold` `40→32`（10-23 优化期开启告警） | ✅ 生效；**后于 20:26 因"总弹缓存偏低"把 `cache_hit_rate_alert_enabled` 关回默认 false + 阈值 0**（插件默认即关闭、避免刷屏；命中率≈46% 是结构性基线非故障） |
| 历史窗口 | spectrecore `group_msg_history 8→6` | ✅ 生效 |
| 知识库更新 | `faq.json` 21→23 条（更新 6 旧 + 新增 `browser`/`play-guide`，含完整《游玩指引》） | ✅ 现读生效（备份 `faq.json.bak-20261023-1722`） |
| 只读状态桥 | 宿主机 `bot_proxy.py` + systemd `stronghold-bot-proxy`（172.18.0.1:31999），机器人经此查宿主机状态 | ✅ 自启 active |

---

## 7. 待办 / 已知问题

1. **snowluma 个人号（服务器容器）Exited(0)**（12:38 GPU 崩溃 `GPU process isn't usable`+SIGTRAP）。**注意**：真正在用的 SnowLuma 是本机 Windows 实例（`snowluma相关/runtime-v1.14.22/`，带 GPU、活着），面板「个人提醒→本地 SnowLuma 日志」可读它。服务器那个容器是否还用、要不要拉起（建议 `--disable-gpu`）**待用户确认，疑似遗留**——别急着动，先问。
2. **RAG 知识库 `kb.db` 仍空**：要让 LLM 走语义检索答疑，需先配 embedding provider 再经 astrbot 文档上传，当前 `faq.json` 关键词直答已够用。
3. **游戏 AI 队友"变肥鱼"插件不可行**：游戏插件系统禁止改服务端逻辑（`no sim or server plugin code runs`）。要改只能直接动 `lobby.js`/`botEmotes.js` 三档方案（改名/换皮/真说话），且须低峰重启游戏服。优先级最低。
4. **admission 准入闸进一步决策**：内存加固后余量宽裕，是否继续上调 `maxRssMB`/`minAvailableMB` 待定（玩法体验问题，非钱的问题）。

---

## 8. 🚫 给 Agent 的红线与约定（务必遵守）

1. **禁止随意重启**：系统/游戏服重启在 2GB 机器上风险高（swap 颠簸、SSH 卡死、踢玩家）。改配置优先选"现读生效"或"下次自然重启"，确需重启须先告知用户并取得同意。
2. **禁止给机器人 shell / 写文件权限**：群消息不可信，LLM 持宿主机 shell = prompt injection 直出任意命令。动代码一律走 WorkBuddy 经 SSH + 用户 review。机器人的角色是"诊断 + 播报"，不是"自动改服务器"。
3. **改 FAQ 改 `faq.json`**：现读生效、零风险，是加"知识"的首选路径；别去碰 `kb.db`。
4. **改准入闸改 `start.mjs`**：必须重启游戏服才生效，会踢人——先问用户能否接受中断。
5. **备份优先**：任何线上文件改动前先 `cp 原 .bak-日期-时间`，回滚 = cp 回去 + restart。
6. **敏感信息不落盘、不进 LLM 上下文**：私钥 `.robot-nopass.pem`、口令 `Chen051107*`（密码.md）只用于建立 SSH，不要写进任何共享文档或交给模型记忆。
7. **缓存命中率 ~46% 是基线，不是故障**：根因是 spectrecore 把群聊历史拼进 system_prompt，属固有代价；不要为每周几分钱去做高危的 prompt 重构。
8. **诊断报告里的"system_prompt_change/context_reset"多为人为重启造成**：今天为改配置重启会自造 context_reset，别被报告误导去过度优化。

---

## 9. 常用操作速查

```bash
# 连主机（单条命令）
ssh -i "/f/github/BOT用/.robot-nopass.pem" -o StrictHostKeyChecking=no root@game.lingluotoki.dpdns.org "uptime; free -m; systemctl is-active stronghold.service stronghold-3001.service stronghold-bot-proxy"

# 公网健康检测（玩家视角，无需 SSH）
curl -s http://game.lingluotoki.dpdns.org/healthz

# 重启各服务（按需，注意踢人/颠簸）
systemctl restart stronghold.service stronghold-3001.service   # 游戏服（踢人）
systemctl restart stronghold-control                            # 内存监控（observe.py 阈值改后）
systemctl restart stronghold-bot-proxy                          # 只读桥
# astrbot 在容器内，重启用 docker restart <astrbot容器名> 或对应 systemd

# 查准入闸当前生效参数
cat /run/stronghold-stability/admission-3000.json

# 拉起 snowluma（恢复个人号，建议 --disable-gpu）
# 具体命令依容器内启动方式，需先 docker ps 确认容器名与启动参数
```

---

## 10. 给新对话的 30 秒摘要（直接复制到新 chat 当背景）

> 我在维护"大肥鱼"——一个 AstrBot QQ 群机器人，服务于同人游戏《卫戍协议：盟约》，机器人和游戏服同跑在阿里云 2GB 轻量服务器（域名 game.lingluotoki.dpdns.org，root 登录，私钥 F:\github\BOT用\robot.pem 口令见密码.md，去口令副本 .robot-nopass.pem）。机器人知识库是 astrbot 容器内 `plugins/astrbot_plugin_wushu_ops/faq.json`（现读生效，改完不用重启）；成本/人格配置在 `plugin_data` 与 `config/`；游戏服入口 `/opt/stronghold-stability/start.mjs`（改准入闸要重启游戏服、会踢人）；内存监控 `/opt/stronghold-stability/observe.py`。**红线：2GB 机器别乱重启、别给机器人 shell 权限、敏感私钥/口令不进 LLM。** 详见同目录 `BOT项目交接手册.md`。

---

## 变更记录

- **2026-10-23**：初版建立。沉淀架构、访问方式、关键文件路径、已做改动、红线约定、常用命令、30 秒摘要。覆盖本轮全部运维动作（内存加固、准入闸放宽、成本护栏、FAQ 游玩指引更新、只读桥）。
