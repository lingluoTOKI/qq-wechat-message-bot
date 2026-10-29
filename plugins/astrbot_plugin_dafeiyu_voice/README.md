# astrbot_plugin_dafeiyu_voice v2.0

大肥鱼的多档回落语音插件（借鉴 VoiceClone Flow 的思路：文字+语音并存、分句、失败优雅降级）。

## 回落链（可配置顺序，默认）

1. **本地 GPT-SoVITS**（经桥接 32000，洛天依音色；PC 开着时用这个）
2. **MiMo**（小米 TTS，可选，需填 `mimo_api_base`）
3. **Edge TTS**（微软免费，`zh-CN-XiaoyiNeural` +15Hz；PC 关机也不哑）

任何一档失败自动降级；全部失败则**只保留文字**，绝不发错音。

## 命令

- `/说 <文本>` —— 用回落链念一段指定的话
- `/语音开` —— 开启自动语音（之后每条回复转语音）
- `/语音关` —— 关闭自动语音
- `/语音状态` —— 查看模式/档位/顺序/输出方式
- `/语音测试` —— 依次测试各档是否可用

## 输出方式（output_mode）

- `append`（默认）：先发原文字，再补发语音 —— 可看可听
- `replace`：只发语音，替换文字

## 前提

- 本地档：本机面板（`启动管理面板.bat`）已启动本地 TTS 客户端 + GPT-SoVITS(9880)
- Edge 档：容器能上网（所需 `edge-tts` 已列入 requirements.txt，插件加载时自动装）
- token：`proxy_token` 与服务器 `local_tts_server` 的 `LTT_TOKEN` 一致
