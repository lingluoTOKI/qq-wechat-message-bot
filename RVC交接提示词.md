# 交接提示词：RVC 歌唱双预设流水线（本机已完成，云端对接待续）

你是接手工程师。请先用 Read 核实下列文件的真实现状，再继续动手；不要凭记忆或本提示词假设，路径以实际为准。用户习惯"你改我测、直接部署不要废话"，交付要带详细注释的完整可运行代码，不要生硬客服话术。

## 一、总体目标（两阶段）
把一套"RVC 歌唱双预设标准化流水线"落地，面向洛天依类 Vocaloid 音色，稳定高质量翻唱。
- 阶段 1（已完成）：本机跑通自动化流水线脚本。
- 阶段 2（进行中，任务 #3）：云端 DS 歌唱系统适配。
**关键澄清（用户原话）：云端不做 RVC 推理，也没有任何音频工具；流程是"本机推理 → 把成品上传到云端"。所以云端任务的实质是"上传/对接"，不是在云端跑 RVC。**

## 二、已完成的本机产物（已实测跑通）
- 主脚本：`F:\github\RVC20260718Nvidia\song_pipeline.py`
  阶段：分轨（单声道自动转立体声兼容）→ 独立去混响 → afftdn 降噪 + acompressor 压缩 → rubberband 预变调 → RVC 推理（已正确传入 Flat index，索引生效）→ EQ + amix 对齐混音 → loudnorm 响度 -14 LUFS、`-ar 44100` 规范成品。
  argparse：`input`、`--model`、`--preset A|B|both`、`--pitch`、`--out`、`--no-sep`、`--workdir`。
- 配置：`F:\github\RVC20260718Nvidia\rvc_presets.json`（双预设 + 软约束区间，云端可复用）。
  预设 A（难句兜底/默认）：rmvpe / rms_mix_rate 0.35 / protect 0.25 / index_rate 0.45。
  预设 B（平稳段求音色）：rmvpe / rms_mix_rate 0.25 / protect 0.33 / index_rate 0.70。
  两者 f0_up_key=0、resample_sr=0、f0method=rmvpe。
- 验证成品（soundfile 复核有效）：
  `F:\github\BOT用\_tmp\test_A_成品.wav`（24.9s/44100Hz，干声链路）
  `F:\github\BOT用\_tmp\test_full_成品.wav`（30.0s/44100Hz，完整分轨链路）
- 测试输入：`F:\github\BOT用\_tmp\test_vocal.wav`（立体声）、`test_full.wav`（单声道）；中间产物在 `_tmp\_pipeline_*` 工作目录。

## 三、关键环境事实
- RVC 整合包根目录：`F:\github\RVC20260718Nvidia`；UVR 便携目录：`F:\github\Ultimate Vocal Remover`（含 rubberband.exe、ffmpeg.exe）。
- 运行解释器（必须用这个，不是 Anaconda）：`F:\github\RVC20260718Nvidia\runtime\python.exe`。
- GPU：NVIDIA RTX 4060 Laptop，torch 2.7.1+cu118，CUDA 可用。
- 洛天依权重：`assets/weights/LuoTianyi-test.pth`；对应 Flat 索引：`logs/added_IVF3329_Flat_nprobe_1_LuoTianyi-test_v2.index`（flat/nprobe1 对歌唱稳定友好，不要换 big-nprobe）。
- 底层推理：`infer/vc/modules.py` 的 `VC`，`vc_single` 为同步方法可直接 import；模型加载用 `get_vc(权重文件名)`；索引路径用 `infer/vc/utils.py` 的 `get_index_path_from_model(权重名)` 自动匹配。
- 软约束（业务层校验，RVC 不拦）：index_rate 0.4~0.8、rms_mix_rate 0.2~0.4、protect 0.2~0.45；fcpe 禁止作为默认（低频缺陷大），仅可作高级可选项。

## 四、本机已踩平的坑（不要重走）
1. 整合包 ffmpeg 4.3.2 无 `compander` 滤镜 → 用同版本支持的 `acompressor`，参数阈值 -15dB、ratio 2、attack 10ms、release 150ms。
2. 首次 RVC 报 `'None/LuoTianyi-test.pth'` → 脚本开头需补齐 webui 才有的环境变量：weight_root / weight_uvr5_root / index_root / outside_index_root / rmvpe_root。
3. 单声道输入直接喂 BS-RoFormer 必崩（reflect pad 报 Only 2D~5D）→ 分轨前检测单声道，先用 ffmpeg `-ac 2` 转立体声。
4. rvc_sep.py 自带的 RVC_DEREVERB 去混响路径有 bug（把目录 `_pre_dereverb` 当文件传给 librosa，Permission denied）→ 改为先普通分轨，再单独对人声文件跑一次 rvc_sep 模型 `VR-DeEchoDeReverb`（第一参数为文件，接口正确）。
5. 初版 vc_single 传空 index 导致日志"索引：未使用"、index_rate 不生效 → 用 get_index_path_from_model 取真实路径传入。
6. loudnorm 后曾出现 192kHz 采样率 → 输出命令统一加 `-ar 44100`。

## 五、云端已探查的真实情况（2026-10-27 SSH 实测）
- 服务器：`root@101.132.104.41`（域名 game.lingluotoki.dpdns.org / game.lingluotoki.top），Ubuntu，内核 5.15。
- SSH 密钥：`F:\github\BOT用\.robot-nopass.pem`，连接加 `-o StrictHostKeyChecking=no`，已验证可登录。
- 云端无 ffmpeg / rubberband / UVR（用户确认），不做推理。
- 无 pm2。Docker 里运行容器：`astrbot`（镜像 soulter/astrbot:pinned-4.29.3），端口 127.0.0.1:6185→6185、127.0.0.1:6199→6199。
- `/root` 下目录：backups、final、patch3000、releases、snap、stronghold、stronghold-3001、stronghold-upload-skins-20261026-182415。
- **尚未定位到明确的"DS 歌唱系统"独立服务/上传接口**，这是下一步第一件事：继续只读探查（看 astrbot 容器内插件、监听端口、nginx 配置、各目录用途），确认成品该以什么方式上传、落到哪个目录、是否需要回调/任务状态字段。先查清再写对接代码，不要猜接口。

## 六、待办（任务 #3 拆解）
1. SSH 继续只读探查，定位 DS 歌唱系统的接收方式（候选：SCP/SFTP 直传到指定目录；HTTP 上传/回调 API；对象存储返回 URL）。以实际存在的机制为准。
2. 产出云端适配：优先复用 rvc_presets.json 的结构化参数；写上传对接模块（是否集成进 song_pipeline 的 `--upload` 一键到底，还是独立 upload 脚本，按探查到的机制和用户确认决定）。
3. 用真实链路验证：本机跑一个成品 → 上传 → 云端确认文件落位、可访问、字段正确；失败要报错不静默。
4. 全程只做用户点名范围，不改动 RVC 底层、不擅自删除/移动用户文件；涉及云端写入先确认目标路径。

## 七、更早的并行遗留（与本主线无关，勿主动执行，仅需知晓）
- 本地消息提醒 BOT 的 GLM 故障已修（glm-5.3-flash 强制思考，thinking 须 enabled + reasoning_effort low；max_output_tokens 2048）。
- 待用户拍板：3271 条历史 analysis_failed 是否清理；relay/config 改动是否提交 GitHub（https://github.com/lingluoTOKI/qq-wechat-message-bot）。
- 杭州备用节点 116.62.39.28 应用层卡死，现有密钥 SSH 失败（exit 255），需有效凭证才能重启，当前搁置。

## 开工要求
先 Read `song_pipeline.py` 与 `rvc_presets.json` 现状；需要云端细节时用密钥 SSH 重新核实，勿凭记忆。完成后给出：产物名、可访问路径、验证方式与覆盖范围、仍存在的缺口。
