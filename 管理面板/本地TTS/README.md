# 本地 TTS（GPT-SoVITS）接入大肥鱼

让大肥鱼用**你自己电脑**上的 GPT-SoVITS 合成语音（洛天依音色，**100% 每次完全一致**）。
**电脑关着时会自动回落**到服务器上的 MiMo / Edge，你无感知。

```
电脑开着  → 本机 GPT-SoVITS（洛天依原声，每次完全一致）
电脑关着  → 服务器 MiMo（洛天依克隆音色）
再失败    → Edge TTS（永久免费兜底）
```

---

## 一、装 GPT-SoVITS（只需做一次）

### ⚠️ 先看版本匹配（三条路，按推荐顺序）

你的洛天依权重是 **2024-08 的 V2**（`GPT_weights_v2` / `SoVITS_weights_v2` 目录结构）。
官方整合包有多个版本，**GPT 权重架构在 v2Pro 改过、v5 又改过一次**：

| 整合包 | 你的 V2 权重能直接用吗 | 速度 | 结论 |
|---|---|---|---|
| **`20240821v2`** | ✅ 直接可用 | 中 | **最省事**：解压→放权重→跑 |
| **`20261005v5`** | ❌ 架构不同，**用不了** | 最快 | 见下方两条替代路 |
| `20250606v2pro` | ⚠️ SoVITS 行，GPT 权重可能不行 | 快 | 折中备份 |

#### 如果你想用 v5（2026-10，最新）
v5 **不能用**你的 V2 微调权重，只有两条路：

**A. v5 零样本克隆（推荐，开箱即用）**
v5 支持零样本：直接给一段参考音频就能克隆。
你的 `参考音频/大家好，我是虚拟歌手洛天依.wav`（5.9 秒）**就是洛天依原声**，
把两段拼起来（10.7 秒）当参考音频喂给 v5 即可，**不用训练任何东西**。
> 效果：比 MiMo 的克隆强不少，但**不如**你那份精心微调的 V2 权重（那份是用调教素材训出来的）。

**B. 用 v5 重新训练洛天依（效果最好，但要花时间）**
需要准备数据集 → 切分 → 标注 → 提特征 → 训 SoVITS → 训 GPT。
工作量大（还要显存和几十分钟到几小时的训练时间），但能得到 v5 架构下的最优音质。

#### 关于 v2Pro（20250604）的兼容性预期

v2Pro 改过 GPT 部分架构，所以：

| 权重 | 能不能加载 |
|---|---|
| `luotianyi_e16_s432.pth`（SoVITS） | ✅ 架构没变，**能加载** |
| `luotianyi-e50.ckpt`（GPT） | ⚠️ 大概率失败（架构不同） |

**不用手动处理**：客户端启动时会
1. 加载你的权重（GPT 失败也没关系）
2. GPT 失败 → **自动扫整合包目录找自带的官方 GPT 权重**并切过去
3. 两个端点都失败 → 仍然可用**零样本模式**：直接用洛天依原声当参考音频克隆

> 效果排序：V2 微调权重 > v2Pro 零样本 > MiMo 克隆。
> 想用你那份精训权重，还是装 `20240821v2` 最稳。

#### 接口差异（客户端已自动适配）

| 版本 | 权重端点 | 合成端点 |
|---|---|---|
| 旧 V2（2024-08） | `POST/GET /set_sovits_weights`（`v2ModelPath`+`v2CheckpointPath`） | `POST /tts`（JSON body） |
| **v2Pro / v5** | `GET /set_gpt_weights?weights_path=` + `GET /set_sovits_weights?weights_path=` | `GET /tts?…`（query 参数） |

客户端会**依次尝试所有形式**，并在面板日志里打印每种方式的实际 HTTP 返回，
照着报错改就行。

#### 下载地址

**v2（与你的权重同代，首选）**

| 渠道 | 链接 |
|---|---|
| HuggingFace | `https://huggingface.co/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v2-240821.7z?download=true` |
| 百度网盘（超级会员满速） | `https://pan.baidu.com/s/1OE5qL0KreO-ASHwm6Zl9gA?pwd=mqpi` 提取码 `mqpi` |
| 联通云盘（免费） | `https://drive.uc.cn/s/a1fd91ae0a4f4` |

**v5（最新最快）**

| 渠道 | 链接 |
|---|---|
| HuggingFace | `https://huggingface.co/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v5-20261005.7z?download=true` |
| hf-mirror 镜像 | `https://hf-mirror.com/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v5-20261005.7z?download=true` |
| 百度网盘 | `https://pan.baidu.com/s/1OE5qL0KreO-ASHwm6Zl9gA?pwd=mqpi` 提取码 `mqpi` |

**v5 的 50 系显卡包**（RTX 5080/5090 等，文件名后缀是 `nvidia50x0`，注意和 v2pro 的 `nvidia50` 不同）：
`https://hf-mirror.com/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v5-20261005nvidia50x0.7z?download=true`

> 解压到**没有中文和空格**的路径，例如 `D:\GPT-SoVITS`。解压后约十几 GB，**先确认磁盘空间**。

### 硬件门槛
- 推理：6GB 显存即可（纯 CPU 也能跑，但每句 15–40 秒，不建议）
- 你要跑的是推理，不需要训练

### 放入你的洛天依权重

在 `F:\github\BOT用\TTS素材\洛天依gptsovits模型\V2\` 里：

| 你的文件 | 放到 |
|---|---|
| `GPT_weights_v2/luotianyi-e50.ckpt` | `GPT_SoVITS/`（GPT 权重） |
| `SoVITS_weights_v2/luotianyi_e16_s432.pth` | `GPT_SoVITS/pretrained_models/`（SoVITS 权重） |

> **不需要改名**——客户端脚本会直接按路径传（`v2ModelPath` / `v2CheckpointPath`）。
> 若你的整合包版本要求官方约定名，脚本启动时会打印服务器的报错，照着改即可。

> ⚠️ 一定要用**不带 `old`** 的那套权重（`luotianyi-e50` / `luotianyi_e16_s432`），
> `old` 那套是用语调素材训练的，听起来更机械。

### 确认底模齐全

整合包通常自带底模。检查 `GPT_SoVITS\pretrained_models\` 下有 `gsv-v2final-pretrained`；
缺的话从 https://huggingface.co/lj1995/GPT-SoVITS-models 补齐，
G2PW 模型（首次启动会自动下约 600MB，不用管）。

### 启动 API

在整合包根目录运行：

```bat
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

看到 `Uvicorn running on http://127.0.0.1:9880` 就算好了。

## 二、填配置

编辑同目录的 `config.json`（**也可以在管理面板里改**）：

```json
{
  "gpt_model_path": "D:\\GPT-SoVITS\\GPT_SoVITS\\s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
  "gpt_ckpt_path":  "D:\\GPT-SoVITS\\GPT_SoVITS\\pretrained_models\\s1v3.pth",
  "ref_audio":      "F:\\github\\BOT用\\TTS素材\\洛天依gptsovits模型\\V2\\参考音频\\大家好，我是虚拟歌手洛天依.wav",
  "prompt_text":    "大家好，我是虚拟歌手洛天依"
}
```

> `ref_audio` 必须配**「怎么还是这张图捏，能不能换一张呀.wav」**或
> 「大家好，我是虚拟歌手洛天依.wav」之一，`prompt_text` 要和音频里说的一致。

## 三、启动客户端

**方式 A（推荐）**：打开管理面板 → 「本地语音」区块 → 点「启动客户端」

**方式 B**：命令行

```bat
python local_tts_client.py
```

看到 `→ 已连上服务器，等待任务…` 就成功了。

---

## 排错

| 现象 | 原因 |
|---|---|
| `权重加载失败` | GPT-SoVITS 的 api 没起；或权重路径/命名不对（脚本会把服务器的报错打出来，照着改） |
| `本地 TTS 不可用(408)` | 客户端没连上服务器（检查 `server_url` 与 `server_token`） |
| 合成很慢 | 没装 GPU 加速；或用的是 `old` 那套权重 |
| 机器人还是用 MiMo | 客户端没运行（面板上看状态），或服务器侧 `tts_prefer_local` 被关了 |

---

## 工作原理

```
 AstrBot(容器) ──HTTP──► local_tts_server(宿主机 systemd) ◄──长轮询── 你的电脑
      │                        │                                          │
      │ 拿不到就在 8 秒内回落   │ 任务队列                                   │ 调本机
      └────────────────────────┴──────────────────────────────────────────┘
```

服务器侧不预判电脑在不在：**发任务 → 8 秒没人领就自动回落**，所以开关机完全无感。


---

## 当前使用的模型（2026-10-26 已装好并跑通）

**洛天依 13 周年生日会 v2Pro 模型**（B站「只想干饭的麦克斯韦妖」制作）
来源：https://github.com/xdedmmc2/GPT-SoVITS-Luo-Tianyi

### 已就位

| 项 | 路径 |
|---|---|
| GPT 权重 | `GPT_SoVITS\..\GPT_weights_v2Pro\luotianyi13_e15.ckpt` |
| SoVITS 权重 | `SoVITS_weights_v2Pro\luotianyi13_e8_s384.pth` |
| 参考音频 | `refs\luotianyi13_ref.wav`（3.02s / 32kHz 单声道） |
| 参考文本 | `我现在需要一个长达三秒的语音` |

原始文件名含中文和长破折号，已重命名为 ASCII——**API 通过 query 参数传路径，
非 ASCII 容易踩编码问题**。

### 关键配置：`custom` 节必须切到 v2Pro

`GPT_SoVITS/configs/tts_infer.yaml` 的 `custom` 节是**唯一生效**的一节
（`TTS_Config` 里 `configs_.get("custom", ...)`）。原值是 `version: v2` +
v2 底模路径，**直接加载 v2Pro 微调权重会失败**。已改为：

```yaml
custom:
  version: v2Pro
  device: cuda          # RTX 4060 Laptop
  is_half: true
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt
  vits_weights_path: GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth
```

底模留在 yaml 里不动，微调权重由 API 的 `set_gpt_weights` /
`set_sovits_weights` 在运行时替换——这样**万一微调权重有问题，API 仍能起来**，便于排查。

### 实测性能（RTX 4060 Laptop）

| 指标 | 结果 |
|---|---|
| 权重加载 | 1.1s + 1.5s |
| 首次合成（含预热） | 13.6s |
| 稳定后合成 | **2~3 秒 / 句**（RTF **0.45**，比实时快一倍多） |
| 输出 | 32000Hz 单声道 WAV |

### 一键启动

双击 `启动本地语音.bat`：会先探测 9880 端口，没起就拉 GPT-SoVITS API，
再启动客户端连服务器。

### ⚠️ 模型使用协议

该模型仅允许**个人学习、非公开创作、同好交流**；**禁止商业用途**，
禁止生成政治敏感/违法/色情暴力内容，禁止冒充真人。
本群是私有同好群、无任何收费，符合协议范围。二次发布需注明原作者与仓库链接。
