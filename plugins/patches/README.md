# 第三方插件本地补丁

更新第三方插件后需要重打的补丁。

| 补丁 | 目标插件 | 作用 |
|---|---|---|
| `patch_proactive_chat_budget.py` | `astrbot_plugin_proactive_chat` | 在 `core/chat_flow.py` 注入 `_budget_status()` 前置判断：主动消息走 `provider.text_chat()` 直连、绕过 cost_control 的 `on_llm_request` 钩子，超出全局日预算（token 维度 + 金额维度）时静默跳过并重新调度。 |
| `fix_proactive_chat_budget_schedule.py` | 同上 | 修正时段倍率（峰时 ×2）的 weekdays 过滤。执行顺序：**先 patch，后 fix**。 |
| `plugin_optimization_20261029/astrbot_plugin_bilibili/main.py` | `astrbot_plugin_bilibili` | 全量副本（2026-11-01 更新）：启动时复用 BiliRead 的 sessdata；bgm/B站搜索类 5 个 LLM 工具改为默认不注册（配置项 `llm_tools_enabled`），降低每轮固定 token。 |
| `plugin_optimization_20261029/astrbot_plugin_biliread/main.py` | `astrbot_plugin_biliread` | 全量副本（2026-11-01 更新）：视频字幕/评论改为注入 `extra_user_content_parts` 临时片段，不再改写 system prompt，避免 DeepSeek 前缀缓存整段失效。 |
| `bilibili_private_20261029.patch` | `astrbot_plugin_bilibili` | 可重放补丁（基线 v1.7.2）：复用 BiliRead 的 sessdata + 5 个 LLM 工具默认不注册。上游升版后 `git apply` 重打即可，不必整目录覆盖。 |

应用方式（服务器上）：

```bash
cd /opt/bot-stack/astrbot/data/plugins/astrbot_plugin_proactive_chat
cp core/chat_flow.py core/chat_flow.py.bak-budget-$(date +%Y%m%d)
python3 /tmp/patch_proactive_chat_budget.py
python3 /tmp/fix_proactive_chat_budget_schedule.py
```

改完在面板重载 `astrbot_plugin_proactive_chat` 生效（不要重启容器，会清前缀缓存）。

## 私有插件保护清单（2026-11-16）

以下 5 个第三方插件已被改造，**不要在面板点「更新」**——市场更新是整目录覆盖，改动会全丢。
副本存于 `plugin_optimization_20261029/`，已与服务器逐文件核对一致。

| 插件 | 与上游差异 | 更新策略 |
|---|---|---|
| `astrbot_plugin_bilibili` | 2 文件 / 约 82 行 | 可用 `bilibili_private_20261029.patch` 重放，允许跟版 |
| `astrbot_plugin_biliread` | 5,954 行 | 冻结，不更新 |
| `astrbot_plugin_bilivideo` | 6,666 行 / 44 文件 | 冻结，不更新 |
| `astrbot_plugin_bili_dynamic` | 1,464 行 | 冻结，不更新 |
| `astrbot_plugin_bilibili_ai_bot` | 53 改 + 36 新增（本地 1.7.2 vs 上游 2.3.2） | 冻结，不更新 |

误点更新后的恢复方式：

1. 从本仓库对应目录整体拷回服务器 `data/plugins/`；
2. 或解压 `/root/plugins-backup-20261116.tar.gz`（30 个插件全量，43 MB）。

补丁用法：

```bash
cd astrbot_plugin_bilibili
git apply --ignore-whitespace --whitespace=nowarn bilibili_private_20261029.patch
```

