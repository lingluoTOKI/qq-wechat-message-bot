# 第三方插件本地补丁

更新第三方插件后需要重打的补丁。

| 补丁 | 目标插件 | 作用 |
|---|---|---|
| `patch_proactive_chat_budget.py` | `astrbot_plugin_proactive_chat` | 在 `core/chat_flow.py` 注入 `_budget_status()` 前置判断：主动消息走 `provider.text_chat()` 直连、绕过 cost_control 的 `on_llm_request` 钩子，超出全局日预算（token 维度 + 金额维度）时静默跳过并重新调度。 |
| `fix_proactive_chat_budget_schedule.py` | 同上 | 修正时段倍率（峰时 ×2）的 weekdays 过滤。执行顺序：**先 patch，后 fix**。 |
| `plugin_optimization_20261029/astrbot_plugin_bilibili/main.py` | `astrbot_plugin_bilibili` | 全量副本（2026-11-01 更新）：启动时复用 BiliRead 的 sessdata；bgm/B站搜索类 5 个 LLM 工具改为默认不注册（配置项 `llm_tools_enabled`），降低每轮固定 token。 |
| `plugin_optimization_20261029/astrbot_plugin_biliread/main.py` | `astrbot_plugin_biliread` | 全量副本（2026-11-01 更新）：视频字幕/评论改为注入 `extra_user_content_parts` 临时片段，不再改写 system prompt，避免 DeepSeek 前缀缓存整段失效。 |

应用方式（服务器上）：

```bash
cd /opt/bot-stack/astrbot/data/plugins/astrbot_plugin_proactive_chat
cp core/chat_flow.py core/chat_flow.py.bak-budget-$(date +%Y%m%d)
python3 /tmp/patch_proactive_chat_budget.py
python3 /tmp/fix_proactive_chat_budget_schedule.py
```

改完在面板重载 `astrbot_plugin_proactive_chat` 生效（不要重启容器，会清前缀缓存）。
