#!/usr/bin/env python3
"""本地 TTS 客户端（在你自己电脑上跑）

作用：把本机运行的 GPT-SoVITS 接到服务器上的大肥鱼。
      电脑开着 → 大肥鱼用你的 GPT-SoVITS（音色 100% 原汁原味、每次完全一致）
      电脑关着 → 服务器 8 秒内没等到结果，自动回落到 MiMo / Edge

用法
----
1. 先启动 GPT-SoVITS 的 api_v2.py（默认 http://127.0.0.1:9880）
2. 改好下面「配置区」的四项
3. python local_tts_client.py

只用 Python 标准库，不需要 pip install 任何东西。
"""
import base64
import shutil
import tempfile
import threading
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 本机请求一律绕过系统代理 ----
# 踩过的坑：机器上装了代理工具时 HTTP_PROXY 连 127.0.0.1 的请求也一起劫持，
# 于是隧道明明通着，客户端却一路抛"没有可用的服务器通道"，服务器队列永远没人领 →
# 群里点歌就回"本机没接单"。
# 注意：Windows 下 urllib.request.proxy_bypass 只读注册表 ProxyOverride，**不认
# no_proxy 环境变量**，所以光设环境变量没用，得把这个判断包一层。
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
_orig_proxy_bypass = urllib.request.proxy_bypass


def _bypass_local(host):
    if not host:
        return True
    h = str(host).split(":")[0].strip("[]")
    if h in ("127.0.0.1", "localhost", "::1") or h.startswith("127."):
        return True
    try:
        return _orig_proxy_bypass(host)
    except Exception:
        return False


urllib.request.proxy_bypass = _bypass_local

# ---- 输出编码兜底（务必放在任何 print 之前）----
# 面板把 stdout 重定向到文件时，Python 按系统代码页（中文 Windows = GBK）编码。
# 任务文本里只要出现颜文字的半角片假名（如 ﾟ U+FF9F），print 就会抛
# UnicodeEncodeError 并让整个客户端进程退出 —— 之后服务器一直 408。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg=""):
    """安全打印：任何编码问题都不允许弄崩客户端。"""
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


# ---- 忙标记 + 忙时心跳（防止面板看门狗把正在干活的客户端杀掉）----
# 踩过的坑：翻唱一首歌要 3~5 分钟，这期间客户端不 poll，服务器 health 的
# online 判定（30 秒内有无 poll）会变成 False → 面板看门狗以为它挂死 →
# taskkill 掉 → 歌明明混音完成却回传不了，群里只看到"翻唱失败/timed out"。
# 对策两条：
#   1) 干活期间写 BUSY_FILE，看门狗看到就跳过重启；
#   2) 干活期间每 10 秒 POST /ping 保鲜服务器的 _last_poll，health 保持 online。
BUSY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".busy")
_BUSY = threading.Event()


def _busy_set(stage=""):
    _BUSY.set()
    try:
        with open(BUSY_FILE, "w", encoding="utf-8") as f:
            f.write("%.0f %s" % (time.time(), stage or "task"))
    except Exception:
        pass


def _busy_clear():
    _BUSY.clear()
    try:
        if os.path.exists(BUSY_FILE):
            os.remove(BUSY_FILE)
    except Exception:
        pass


def _heartbeat_loop():
    """忙时每 10 秒给服务器 /ping 一次，让 health 的 online 保持 True。"""
    while True:
        try:
            if _BUSY.is_set():
                base = _base()
                body = json.dumps({"busy": 1}).encode("utf-8")
                req = urllib.request.Request(
                    _url_of(base, "/ping"), data=body,
                    headers={"Content-Type": "application/json",
                             "X-TTSToken": SERVER_TOKEN or ""},
                    method="POST")
                try:
                    urllib.request.urlopen(req, timeout=6).read()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(10)


def _url_of(base, path):
    return (base or "").rstrip("/") + path

# ====================== 配置区（改这里或 config.json） ======================
# 1) 服务器上的代理地址（公网可达）
SERVER_URL = "https://game.lingluotoki.dpdns.org/tts"
# 本地 SSH 隧道地址（面板的隧道会转发 32000）。优先用它：纯 HTTP 走 SSH，
# 不受本机 fake-ip DNS / TLS 拦截影响。留空则只用 SERVER_URL。
SERVER_URL_LOCAL = "http://127.0.0.1:32000"
BASE_URL = None          # 解析后的实际地址（运行时决定）
_LAST_CHAN_ERR = []      # 上次通道探测失败原因（去重用，避免刷屏）
SERVER_TOKEN = ""              # 与服务器 LTT_TOKEN 一致

# 2) 本机 GPT-SoVITS 的 API
GPT_API = "http://127.0.0.1:9880"
# GPT-SoVITS 安装目录（客户端会在 API 没起来时自动拉起它）
GSV_ROOT = r"F:\github\GPT-SoVITS-v2pro-20250604"
# V2 权重路径（按官方约定放到 GPT-SoVITS 目录下）
GPT_MODEL_PATH = r""          # 例：D:\GPT-SoVITS\GPT_SoVITS\s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt
GPT_CKPT_PATH = r""           # 例：D:\GPT-SoVITS\GPT_SoVITS\pretrained_models\s1v3.pth
# 参考音频与其对应文本（用于音色引导）
REF_AUDIO = r""               # 例：F:\...\洛天依gptsovits模型\V2\参考音频\大家好，我是虚拟歌手洛天依.wav
PROMPT_TEXT = "大家好，我是虚拟歌手洛天依"
PROMPT_LANG = "zh"
# ============================================================

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
# ---- 音频转换（RVC 翻唱）----
# backend: mock = 原样返回（联调用）；gradio = 连 RVC WebUI
RVC_BACKEND = "mock"
RVC_API_URL = "http://127.0.0.1:7865/"
RVC_MODEL = "LuoTianyi-test.pth"
RVC_INDEX = ""
RVC_PITCH = 0            # 半音；男转女一般 +12
RVC_INDEX_RATE = 0.40    # 检索比例：0.75 会把音高一起带偏（发飘/怪音），0.40 是盲听最稳的一档
# 清辅音保护（RVC 的 protect）。⚠ 语义是【反】的，别再搞错：
#   pipeline.py: `if protect < 0.5` 才走保护分支，
#   feats = feats(转换后)*protect + feats0(原始)*(1-protect)  ← 只作用于清辅音/静音帧
#   → protect 越小，清辅音越保留原声，越不容易电音撕裂/爆破
#   → protect = 0.5 是【完全关闭保护】（官方文档原话："拉满0.5不开启"）
# 曾经为了防爆破把它调到 0.5，实际等于把保护关掉，杂音正是这么来的。
# 官方默认 0.33；0.25 保护更强但换声效果略淡。
RVC_PROTECT = 0.33
# ⚠ 本版 RVC（infer/vc/pipeline.py 里 grep 不到 filter_radius）已移除该参数，
#   老教程里的"音高中值滤波"在这里不存在，传了也会被忽略。
#   infer_convert 第 7 参实际是 resample_sr：0=不重采样，>=16000 才生效。
RVC_RESAMPLE_SR = 0
RVC_F0_METHOD = "rmvpe"   # pm / rmvpe / fcpe；fcpe 更平滑，可作"跑调"的替代解法
# 音量包络融合：越接近 1 越用【输出】包络（响度更平但可能压掉原曲动态），
# 越接近 0 越跟随输入源。官方默认 0.25。
RVC_RMS_MIX_RATE = 0.25
# 混音响度处理方式：linear（推荐，两遍恒定增益）/ dynamic（逐帧，会抽吸）/ off
MIX_LOUDNORM = "linear"
MIX_TARGET_LUFS = -14.0
# RVC 输出的人声高频严重不足（实测 6~11k 能量只有原人声的 1/3），听感发闷，
# 且残留高频是声码器噪声。这里只给【人声轨】补高频（伴奏本来就够，别动）。
# 实测 7k+3 / 11k+5 能把高频能量补回 0.225%（原人声 0.178%），最接近。
# VOCAL_EQ = "equalizer=f=7000:t=q:w=1:g=3,equalizer=f=11000:t=q:w=1:g=5"
# 关掉：固定 7k/11k 峰会把高音"飞"的共振峰抬成哨音，被误听成跑调（D2 无此 EQ 所以干净）
VOCAL_EQ = ""
# 输出 mp3 码率。鼓点瞬态最吃码率，192k 在密集鼓点上会糊/过冲，256k 稳。
OUT_MP3_KBPS = 256
# 混音目标真峰值（dBTP）。-1.0 太贴天花板，后面转 mp3 再过冲就容易削出爆音。
OUT_TP = -1.5
COVER_MAX_SECONDS = 300  # 超过就截断，避免一次跑太久
RVC_ROOT = "F:/github/RVC20260718Nvidia"     # 整合包根目录（分轨脚本在这里）
RVC_SEP_MODEL = "HP5_only_main_vocal"        # 只留主唱，最适合换音色
RVC_INST_GAIN = 0.45                         # 混回伴奏的音量
# 和声：only_main_vocal 会把和声留在伴奏轨里，等于和声还是原唱的嗓子，
# 跟洛天依主唱叠在一起很违和。开起来就再拆一遍，和声单独成轨也过 RVC。
# 代价：多一次分离 + 一次推理，一首歌大概多 1 分钟。
RVC_HARMONY = True
RVC_HARMONY_GAIN = 0.8                       # 和声混回去的音量（比主唱略低）
RVC_SEP_MODEL2 = "HP5_only_main_vocal"       # 第二遍分离用的模型（拆和声/纯伴奏）
# 和声本来就比主唱轻 6~12 dB，阈值卡太死会把真和声误判成"没有"。
# 实测：空轨/残留 -80~-91 dB，真和声 -30~-50 dB → 取 -55 兜底。
HARMONY_MIN_DB = -55.0
FFMPEG = ""     # 空 = 自动探测（配置 → PATH → <gsv_root>/runtime/）
FFPROBE = ""

# ---- 交接文档资产（RVC交接提示词.md）接入 ----
# 双预设文件：A=难句兜底(稳定性优先) / B=平稳段(音色优先)，内含软约束区间。
# 预设参数 + 约束全部以这个文件为准，别在代码里再抄一份（抄一份就会漂）。
RVC_PRESETS_FILE = "F:/github/RVC20260718Nvidia/rvc_presets.json"
RVC_PRESET = ""            # ""=用面板参数；"A"/"B"=套用预设（插件可按场景指定）
# 变调方式：pre = rubberband 离线预变调（RVC 内部变调恒 0，文档硬约束）
#          rvc = 旧行为，直接把 pitch 当 f0_up_key 传给 RVC（不推荐）
RVC_PITCH_MODE = "pre"
# rubberband（高质量离线变调）。找不到就退回 ffmpeg 的 asetrate+atempo。
RUBBERBAND = "F:/github/Ultimate Vocal Remover/rubberband.exe"
# direct 后端用的流水线脚本（文档标准链路，绕开 Gradio WebUI）
RVC_PIPELINE = "F:/github/RVC20260718Nvidia/song_pipeline.py"
# direct 后端默认分轨模型：BS-RoFormer（SOTA，人声最干净，比老 HP5 慢）
RVC_SEP_MODEL_BS = "model_bs_roformer_ep_317_sdr_12.9755"
# direct 后端实际用的分轨模型（可配置；缺模型时流水线会自动报错，不会静默出错音）
RVC_SEP_MODEL_DIRECT = RVC_SEP_MODEL_BS
# 去混响开关：分轨后对人声再跑一遍 VR-DeEchoDeReverb（现场版/B站音源更干净）
RVC_DEREBVERB = False



def _resolve_fftools():
    """定位 ffmpeg / ffprobe。整合包通常自带在 runtime/ 下。"""
    global FFMPEG, FFPROBE

    def pick(name, cur):
        # 1) 配置里的值（可能是绝对路径，也可能只是命令名）
        if cur:
            if os.path.isfile(cur):
                return cur
            w = shutil.which(cur)
            if w:
                return w
        # 2) PATH
        w = shutil.which(name)
        if w:
            return w
        # 3) 整合包常见位置
        roots = []
        if GSV_ROOT:
            roots.append(GSV_ROOT)
        if RVC_API_URL and os.path.isdir(RVC_API_URL):
            roots.append(RVC_API_URL)
        for r in roots:
            for sub in ("runtime", ".", "bin", "ffmpeg", os.path.join("assets", "ffmpeg")):
                for fn in (name + ".exe", name):
                    p = os.path.join(r, sub, fn)
                    if os.path.isfile(p):
                        return p
        return ""

    FFMPEG = pick("ffmpeg", FFMPEG)
    FFPROBE = pick("ffprobe", FFPROBE)


def _load_config():
    """config.json 里的值覆盖上面的默认值（管理面板会写这个文件）"""
    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH
    global SERVER_URL_LOCAL
    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG, GSV_ROOT
    global RVC_BACKEND, RVC_API_URL, RVC_MODEL, RVC_INDEX
    global RVC_PITCH, RVC_INDEX_RATE, COVER_MAX_SECONDS, FFMPEG, FFPROBE
    global RVC_ROOT, RVC_SEP_MODEL, RVC_INST_GAIN
    global RVC_PROTECT, OUT_MP3_KBPS, OUT_TP
    global RVC_HARMONY, RVC_HARMONY_GAIN, RVC_SEP_MODEL2, HARMONY_MIN_DB
    global RVC_RESAMPLE_SR, RVC_F0_METHOD, RVC_RMS_MIX_RATE
    global RVC_PRESETS_FILE, RVC_PRESET, RVC_PITCH_MODE, RUBBERBAND, RVC_PIPELINE
    global RVC_DEREBVERB, RVC_SEP_MODEL_DIRECT
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            c = json.load(f)
    except Exception:
        return
    SERVER_URL = c.get("server_url") or SERVER_URL
    SERVER_URL_LOCAL = c.get("server_url_local") or SERVER_URL_LOCAL
    SERVER_TOKEN = c.get("server_token") or SERVER_TOKEN
    GPT_API = c.get("gpt_api") or GPT_API
    GSV_ROOT = c.get("gsv_root") or GSV_ROOT
    GPT_MODEL_PATH = c.get("gpt_model_path") or GPT_MODEL_PATH
    GPT_CKPT_PATH = c.get("gpt_ckpt_path") or GPT_CKPT_PATH
    REF_AUDIO = c.get("ref_audio") or REF_AUDIO
    PROMPT_TEXT = c.get("prompt_text") or PROMPT_TEXT
    PROMPT_LANG = c.get("prompt_lang") or PROMPT_LANG
    RVC_BACKEND = str(c.get("rvc_backend") or RVC_BACKEND).lower()
    RVC_API_URL = c.get("rvc_api_url") or RVC_API_URL
    RVC_MODEL = c.get("rvc_model") or RVC_MODEL
    RVC_INDEX = c.get("rvc_index") or RVC_INDEX
    try:
        RVC_PITCH = int(c.get("rvc_pitch", RVC_PITCH))
    except (TypeError, ValueError):
        pass
    try:
        RVC_INDEX_RATE = float(c.get("rvc_index_rate", RVC_INDEX_RATE))
    except (TypeError, ValueError):
        pass
    try:
        COVER_MAX_SECONDS = int(c.get("cover_max_seconds", COVER_MAX_SECONDS))
    except (TypeError, ValueError):
        pass
    RVC_ROOT = c.get("rvc_root") or RVC_ROOT
    RVC_SEP_MODEL = c.get("rvc_sep_model") or RVC_SEP_MODEL
    try:
        RVC_INST_GAIN = float(c.get("rvc_inst_gain", RVC_INST_GAIN))
    except (TypeError, ValueError):
        pass
    try:
        RVC_PROTECT = float(c.get("rvc_protect", RVC_PROTECT))
    except (TypeError, ValueError):
        pass
    try:
        OUT_MP3_KBPS = int(c.get("out_mp3_kbps", OUT_MP3_KBPS))
    except (TypeError, ValueError):
        pass
    try:
        OUT_TP = float(c.get("out_tp", OUT_TP))
    except (TypeError, ValueError):
        pass
    # 去混响：现场版 / B 站音源开起来人声更干净，代价是多跑一遍（约翻倍时间）。
    # ⚠ 不用 rvc_sep.py 自带的 RVC_DEREVERB=1 开关：那条老路径把【目录】当文件
    #   传给 librosa，会报 Permission denied。正确做法见 _dereverb_vocal()。
    os.environ.pop("RVC_DEREVERB", None)
    RVC_DEREBVERB = bool(c.get("rvc_dereverb", RVC_DEREBVERB))
    if c.get("rvc_dereverb_model"):
        os.environ["RVC_DEREVERB_MODEL"] = str(c["rvc_dereverb_model"])
    RVC_HARMONY = bool(c.get("rvc_harmony", RVC_HARMONY))
    RVC_SEP_MODEL2 = c.get("rvc_sep_model2") or RVC_SEP_MODEL2
    try:
        RVC_HARMONY_GAIN = float(c.get("rvc_harmony_gain", RVC_HARMONY_GAIN))
    except (TypeError, ValueError):
        pass
    try:
        HARMONY_MIN_DB = float(c.get("harmony_min_db", HARMONY_MIN_DB))
    except (TypeError, ValueError):
        pass
    try:
        RVC_RESAMPLE_SR = int(c.get("rvc_resample_sr", RVC_RESAMPLE_SR))
    except (TypeError, ValueError):
        pass
    try:
        RVC_F0_METHOD = str(c.get("rvc_f0_method", RVC_F0_METHOD))
    except (TypeError, ValueError):
        pass
    try:
        RVC_RMS_MIX_RATE = float(c.get("rvc_rms_mix_rate", RVC_RMS_MIX_RATE))
    except (TypeError, ValueError):
        pass
    FFMPEG = c.get("ffmpeg") or FFMPEG
    FFPROBE = c.get("ffprobe") or FFPROBE
    # ---- 交接文档资产 ----
    RVC_PRESETS_FILE = c.get("rvc_presets_file") or RVC_PRESETS_FILE
    RVC_PRESET = str(c.get("rvc_preset") or "").strip()
    RVC_PITCH_MODE = str(c.get("rvc_pitch_mode") or RVC_PITCH_MODE).strip().lower()
    RUBBERBAND = c.get("rubberband") or RUBBERBAND
    RVC_PIPELINE = c.get("rvc_pipeline") or RVC_PIPELINE
    RVC_SEP_MODEL_DIRECT = c.get("rvc_sep_model_direct") or RVC_SEP_MODEL_DIRECT
    _resolve_fftools()


def _port_open(host, port, timeout=1.0):
    """探测本机端口是否在监听。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _api_addr():
    """从 GPT_API 解析出 (host, port)。"""
    try:
        p = urllib.parse.urlparse(GPT_API)
        return (p.hostname or "127.0.0.1"), int(p.port or 9880)
    except Exception:
        return "127.0.0.1", 9880


def _ensure_api(wait_seconds=240):
    """确保 GPT-SoVITS API 在跑；没跑就自己拉起来并等它就绪。

    这样面板只需要「启动客户端」一个按钮——不需要用户先手动开 api_v2.py。
    """
    host, port = _api_addr()
    if _port_open(host, port):
        return True

    root = GSV_ROOT
    exe = os.path.join(root, "runtime", "python.exe")
    if not os.path.exists(exe):
        exe = os.path.join(root, "python.exe")
    script = os.path.join(root, "api_v2.py")
    yaml = os.path.join(root, "GPT_SoVITS", "configs", "tts_infer.yaml")

    for path, label in ((exe, "python.exe"), (script, "api_v2.py"), (yaml, "tts_infer.yaml")):
        if not os.path.exists(path):
            print("  !! 找不到 %s：%s" % (label, path))
            print("     请在 config.json / 面板里把 gsv_root 填成 GPT-SoVITS 安装目录")
            return False

    print("→ GPT-SoVITS 未在运行，正在自动启动…")
    try:
        log = open(os.path.join(HERE, "gsv.log"), "ab")
        err = open(os.path.join(HERE, "gsv.err.log"), "ab")
        flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
        proc = subprocess.Popen(
            [exe, script, "-a", host, "-p", str(port), "-c", yaml],
            cwd=root, creationflags=flags,
            stdout=log, stderr=err, stdin=subprocess.DEVNULL)
        print("  已拉起（PID %d），等待模型加载（首次 30~60 秒）…" % proc.pid)
    except Exception as e:
        print("  !! 启动失败：%s: %s" % (type(e).__name__, str(e)[:140]))
        return False

    t0 = time.time()
    while time.time() - t0 < wait_seconds:
        if _port_open(host, port):
            print("  GPT-SoVITS 就绪（%.0f 秒）" % (time.time() - t0))
            return True
        time.sleep(2)
    print("  !! %d 秒内未就绪，请查看 本地TTS/gsv.err.log" % wait_seconds)
    return False


def _req(url, body=None, method=None, headers=None, timeout=20):
    """发一个 JSON 请求，GET/POST 都试（不同版本 GPT-SoVITS 的接口形式不一致）"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _try_set_weights():
    """尝试多种 set_weights 参数形式。

    GPT-SoVITS 不同版本接口不一样（已实测源码）：
      · v2Pro / v5（2025-06 起）：两个独立端点，且是 GET + query 参数
          GET /set_gpt_weights?weights_path=<GPT .ckpt>
          GET /set_sovits_weights?weights_path=<SoVITS .pth>
      · 旧 V2（2024-08）：合并端点，参数 v2ModelPath / v2CheckpointPath
      · 极老版本：weights_path + version
    逐个尝试，返回 (成功方式说明, gpt是否就绪, sovits是否就绪)
    """
    ok_gpt = ok_sovits = False
    how = ""

    def _get(url, params):
        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(url + ("&" if "?" in url else "?") + q,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()

    # --- A. v2Pro / v5 风格：分端点 ---
    try:
        if GPT_MODEL_PATH:
            st, raw = _get(GPT_API.rstrip("/") + "/set_gpt_weights",
                           {"weights_path": os.path.abspath(GPT_MODEL_PATH)})
            if st == 200:
                ok_gpt = True
                how = "set_gpt_weights(GET)"
            else:
                print("  [--] set_gpt_weights → HTTP %s %s" % (st, raw[:120]), flush=True)
        st, raw = _get(GPT_API.rstrip("/") + "/set_sovits_weights",
                       {"weights_path": os.path.abspath(GPT_CKPT_PATH)})
        if st == 200:
            ok_sovits = True
            how = (how + " + set_sovits_weights(GET)").strip(" +")
        else:
            print("  [--] set_sovits_weights → HTTP %s %s" % (st, raw[:120]), flush=True)
    except Exception as e:
        print("  [--] v2Pro 风格 → %s: %s" % (type(e).__name__, str(e)[:110]), flush=True)

    if ok_sovits:
        return how, ok_gpt, ok_sovits

    # --- B. 旧 V2 风格：合并端点 v2ModelPath / v2CheckpointPath ---
    for method in ("GET", "POST"):
        body = {"v2ModelPath": os.path.abspath(GPT_MODEL_PATH),
                "v2CheckpointPath": os.path.abspath(GPT_CKPT_PATH)}
        try:
            st, raw = _try_set_weights_inner(method, body)
            if st == 200:
                print("  [OK] 权重加载成功（旧V2 风格 %s）" % method, flush=True)
                return "旧V2(%s)" % method, True, True
            print("  [--] 旧V2 %s → HTTP %s %s" % (method, st, raw[:110]), flush=True)
        except Exception as e:
            print("  [--] 旧V2 %s → %s: %s" % (method, type(e).__name__, str(e)[:90]), flush=True)

    # --- C. weights_path + version ---
    try:
        st, raw = _try_set_weights_inner("GET", {"weights_path": os.path.abspath(GPT_MODEL_PATH),
                                                 "version": "v2"})
        if st == 200:
            print("  [OK] 权重加载成功（weights_path 风格）", flush=True)
            return "weights_path", True, True
        print("  [--] weights_path → HTTP %s %s" % (st, raw[:110]), flush=True)
    except Exception as e:
        print("  [--] weights_path → %s: %s" % (type(e).__name__, str(e)[:90]), flush=True)

    return "", ok_gpt, ok_sovits


def _find_fallback_weights():
    """用户权重加载失败时，去整合包里找自带的官方权重（按目录扫 .ckpt/.pth）"""
    if not GPT_MODEL_PATH:
        return "", ""
    base = os.path.dirname(os.path.abspath(GPT_MODEL_PATH))
    root = os.path.dirname(base) if base.lower().endswith("gpt_sovits") else base
    cks, pths = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        if any(x in dirpath for x in (".git", "pretrained_models", "logs", "output")):
            continue
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if fn.endswith(".ckpt") and os.path.abspath(p) != os.path.abspath(GPT_MODEL_PATH):
                cks.append(p)
            elif fn.endswith(".pth") and os.path.abspath(p) != os.path.abspath(GPT_CKPT_PATH):
                pths.append(p)
    return (cks[0] if cks else "", pths[0] if pths else "")


def _try_set_weights_inner(method, body):
    url = GPT_API.rstrip("/") + "/set_sovits_weights"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def synth(text):
    """调用本机 GPT-SoVITS 合成，返回 wav 的 base64。
    v2Pro+ 的 /tts 走 GET query 参数，旧版走 POST JSON，两种都试。"""
    body = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": REF_AUDIO,
        "prompt_text": PROMPT_TEXT,
        "prompt_lang": PROMPT_LANG,
        "media_type": "wav",
        "streaming_mode": False,
    }
    base = GPT_API.rstrip("/") + "/tts"
    # A. GET + query（v2Pro/v5 风格）
    try:
        st, raw = _http_get(base, body)
        got = _extract_audio(st, raw)
        if got:
            return got
        print("  [--] /tts GET → HTTP %s %s" % (st, raw[:110]), flush=True)
    except Exception as e:
        print("  [--] /tts GET → %s: %s" % (type(e).__name__, str(e)[:100]), flush=True)
    # B. POST + JSON（旧版风格）
    for method in ("POST", "GET"):
        try:
            st, raw = _try_tts(method, base, body)
            got = _extract_audio(st, raw)
            if got:
                return got
            print("  [--] /tts %s → HTTP %s %s" % (method, st, raw[:110]), flush=True)
        except Exception as e:
            print("  [--] /tts %s → %s: %s" % (method, type(e).__name__, str(e)[:100]), flush=True)
    return None


def _http_get(base, params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(base + "?" + q, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _extract_audio(st, raw):
    """从响应里取出音频：可能是二进制 wav，也可能是 json 里的 base64"""
    if st != 200 or not raw:
        return None
    if raw[:4] == b"RIFF":
        return base64.b64encode(raw).decode()
    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if isinstance(j, dict):
        for k in ("audio", "audio_base64", "data", "wav"):
            v = j.get(k)
            if isinstance(v, str) and v:
                if v.startswith("data:"):
                    v = v.split(",")[-1]
                return v
    return None


def _try_tts(method, url, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _health_of(base, timeout=6, why=None):
    """探一下某个 base 是否可用（/health 返回 200）。

    why 传一个 list 时，把失败原因塞进去 —— 否则通道全挂时只在日志里看到
    「没有可用的服务器通道」，完全不知道是隧道没起、token 过期还是 TLS 被断。
    """
    url = base.rstrip("/") + "/health"
    h = {}
    if SERVER_TOKEN:
        h["X-TTSToken"] = SERVER_TOKEN
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = r.status == 200
            if not ok and why is not None:
                why.append("%s HTTP %s" % (base, r.status))
            return ok
    except urllib.error.HTTPError as e:
        # 403/401 = token 不一致；这条最容易踩又最不直观
        if why is not None:
            why.append("%s HTTP %s（多半是 token 不一致）" % (base, e.code))
        return False
    except Exception as e:
        if why is not None:
            why.append("%s %s: %s" % (base, type(e).__name__, str(e)[:70]))
        return False


def _base(force=False):
    """决定用哪个服务器地址：本地隧道优先，其次公网。

    结果缓存；force=True 时重新探测（隧道断开/恢复时会用到）。
    """
    global BASE_URL, _LAST_CHAN_ERR
    if BASE_URL and not force:
        return BASE_URL
    cands = []
    if SERVER_URL_LOCAL:
        cands.append(("本地隧道", SERVER_URL_LOCAL))
    cands.append(("公网", SERVER_URL))
    errs = []
    for label, base in cands:
        why = []
        if _health_of(base, why=why):
            if BASE_URL != base:
                print("  服务器通道: %s（%s）" % (label, base), flush=True)
            BASE_URL = base
            return base
        errs.extend(why)
    # 全挂时说明原因（只在通道切换/首次探测时打，避免刷屏）
    if errs and not _LAST_CHAN_ERR == errs:
        print("  [通道] 都不通 → %s" % " | ".join(errs), flush=True)
    _LAST_CHAN_ERR = errs
    BASE_URL = None
    return None


def poll():
    base = _base()
    if not base:
        raise RuntimeError("没有可用的服务器通道")
    url = base.rstrip("/") + "/poll"
    body = json.dumps({}).encode()
    h = {"Content-Type": "application/json"}
    if SERVER_TOKEN:
        h["X-TTSToken"] = SERVER_TOKEN
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def post_result(task_id, audio_b64=None, error=None):
    base = _base()
    if not base:
        raise RuntimeError("没有可用的服务器通道")
    url = base.rstrip("/") + "/result"
    payload = {"task_id": task_id}
    if error:
        payload["error"] = error
    else:
        payload["audio_b64"] = audio_b64
    h = {"Content-Type": "application/json"}
    if SERVER_TOKEN:
        h["X-TTSToken"] = SERVER_TOKEN
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


# ==================== 音频转换（RVC） ====================

def _run(cmd, timeout=180):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, (r.stderr or b"")[-400:].decode("utf-8", "replace")
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, str(e)[:150])


def _ensure_wav(src, dst, max_seconds=0):
    """任意音频 → 44.1k 单声道 wav（RVC 对采样率/声道敏感）"""
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "44100"]
    if max_seconds and max_seconds > 0:
        cmd += ["-t", str(max_seconds)]
    cmd += [dst]
    rc, err = _run(cmd, timeout=180)
    ok = rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 2000
    return ok, err


def _wav_seconds(path):
    """拿音频时长；拿不到就返回 0（不做时长限制，不报错）。"""
    if FFPROBE:
        try:
            txt = _probe_stdout([FFPROBE, "-v", "error", "-show_entries",
                                 "format=duration", "-of", "default=nw=1:nk=1", path])
            v = txt.strip().splitlines()
            if v:
                return float(v[-1])
        except Exception:
            pass
    # 回退：从 ffmpeg -i 的 stderr 里抠 Duration
    if FFMPEG:
        try:
            r = subprocess.run([FFMPEG, "-i", path], capture_output=True, timeout=30)
            txt = (r.stderr or b"").decode("utf-8", "replace")
            import re as _re
            m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
            if m:
                h, mi, se = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(se)
        except Exception:
            pass
    return 0.0


def _guess_ext(raw):
    """按文件头猜扩展名（没有 ffmpeg 时靠它给文件一个正确的后缀）。"""
    h = raw[:12]
    if h[:4] == b"RIFF":
        return ".wav"
    if h[:4] == b"OggS":
        return ".ogg"
    if h[:4] == b"fLaC":
        return ".flac"
    if h[:3] == b"ID3" or h[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    if h[:5] == b"#!AMR":
        return ".amr"
    if h[:4] == b"\x02#!S":
        return ".silk"
    if h[4:8] == b"ftyp":
        return ".m4a"
    return ".bin"


def _rvc_post(base, path, data, timeout):
    body = json.dumps({"data": data}).encode("utf-8")
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cover_gradio(src_path, params):
    """RVC WebUI（Gradio 3.14）换音色。纯标准库，已在本机实测通过。

    实测数据（RTX 4060 Laptop）：选模型 0.7 秒，5 秒音频转换 26.9 秒
    （其中 F0 提取 19 秒，首次会加载 rmvpe 模型；之后会快很多）。
    """
    base = RVC_API_URL.rstrip("/")
    pitch = int(params.get("pitch", RVC_PITCH))
    index_rate = float(params.get("index_rate", RVC_INDEX_RATE))

    if not os.path.isfile(src_path):
        return None, "源文件不存在: %s" % src_path

    # 1) 确保模型已加载（幂等，很快）
    protect = float(params.get("protect", RVC_PROTECT))
    if RVC_MODEL:
        try:
            _rvc_post(base, "/api/infer_change_voice",
                      [RVC_MODEL, index_rate, protect], 120)
        except Exception as e:
            print("    [RVC] 选模型失败（继续尝试转换）: %s" % str(e)[:100], flush=True)

    # 2) 内联 base64 传音频——这套整合包没有可用的 /upload
    with open(src_path, "rb") as f:
        raw = f.read()
    name = os.path.basename(src_path)
    audio = {
        "name": name,
        "data": "data:audio/wav;base64," + base64.b64encode(raw).decode(),
        "is_file": False,
        "orig_name": name,
    }

    try:
        # 9 参（顺序来自官方 webui.py 的 but0.click，别按老版本的记忆填）：
        #   sid, audio, pitch, f0方法, index, index_rate,
        #   resample_sr, rms_mix_rate, protect
        #
        # ⚠ 第 7 参在本版本是 resample_sr（0=不重采样，>=16000 才生效），
        #   老版本的 filter_radius 已被官方移除，传进去会被 <16000 静默忽略。
        #   不要再把它当成"音高平滑"用。
        #
        # ⚠ protect 语义是反的：pipeline.py 里 `if protect < 0.5` 才启用保护，
        #   protect=0.5 表示【完全不保护】。值越小，清辅音/呼吸声保留原特征越多，
        #   越不容易出现电音撕裂和爆破杂音（官方文档：拉满0.5不开启）。
        rs = int(params.get("resample_sr", RVC_RESAMPLE_SR))
        f0m = str(params.get("f0_method", RVC_F0_METHOD))
        rms = float(params.get("rms_mix_rate", RVC_RMS_MIX_RATE))
        res = _rvc_post(base, "/api/infer_convert",
                        [0, audio, pitch, f0m, RVC_INDEX, index_rate,
                         rs, rms, protect], 900)
    except Exception as e:
        return None, "转换请求失败: %s" % str(e)[:150]

    data = res.get("data") or []
    if data and isinstance(data[0], str):
        info = data[0].replace("\n", " ")
        print("    [RVC] %s" % info[:120], flush=True)

    for item in data:
        if isinstance(item, dict):
            p = item.get("name") or item.get("path") or ""
            if p and os.path.isfile(p):
                return p, None
            d = item.get("data") or item.get("url") or ""
            if isinstance(d, str) and d.startswith("data:"):
                out = os.path.join(os.path.dirname(src_path), "out.wav")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(d.split(",", 1)[1]))
                return out, None
            if isinstance(d, str) and d.startswith("http"):
                out = os.path.join(os.path.dirname(src_path), "out.wav")
                with urllib.request.urlopen(d, timeout=180) as r, open(out, "wb") as f:
                    f.write(r.read())
                return out, None
        elif isinstance(item, str) and item.lower().endswith((".wav", ".flac", ".mp3", ".ogg")):
            if os.path.isfile(item):
                return item, None

    return None, "转换返回里找不到音频: %s" % (str(data)[:150],)


# ==================== 交接文档资产：预设 / 变调 / 去混响 ====================

def _load_presets():
    """读 rvc_presets.json（双预设 + 软约束）。读不到就返回空 dict，不报错。

    唯一事实来源：整合包根目录下的 rvc_presets.json。
    参数和约束都别在代码里再抄一份——抄一份就会漂，漂了就对不上盲听结论。
    """
    try:
        with open(RVC_PRESETS_FILE, encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("presets"):
            return d
        print("    [预设] %s 里没有 presets 段，忽略" % RVC_PRESETS_FILE, flush=True)
    except Exception as e:
        print("    [预设] 读不到 %s: %s" % (RVC_PRESETS_FILE, str(e)[:90]), flush=True)
    return {}


def _clamp(val, lo, hi, tag):
    """越界就夹回边界并告警（不静默——静默会让用户以为改了其实没改）。"""
    if val < lo:
        print("    [预设] %s=%.3f 低于下限 %.3f，已夹回" % (tag, val, lo), flush=True)
        return lo
    if val > hi:
        print("    [预设] %s=%.3f 高于上限 %.3f，已夹回" % (tag, val, hi), flush=True)
        return hi
    return val


def _resolve_rvc_params(params):
    """合成最终生效的 RVC 参数：面板默认 → 预设 → 请求参数，并做软约束校验。

    优先级：请求里显式带的参数 > 预设 > 面板/配置里的默认值。
    软约束来自 rvc_presets.json 的 constraints 段（交接文档里的业务区间）：
        index_rate 0.4~0.8、rms_mix_rate 0.2~0.4、protect 0.2~0.45、
        f0_method ∈ {pm, rmvpe, fcpe}、resample_sr 恒 0、RVC 内部变调恒 0。

    返回 dict：index_rate / protect / rms_mix_rate / f0_method / resample_sr /
              pitch / preset / separate / harmony
    """
    req = params or {}
    data = _load_presets()
    cons = data.get("constraints") or {}
    presets = data.get("presets") or {}

    # 1) 面板/配置里的默认值
    out = {
        "index_rate": float(RVC_INDEX_RATE),
        "protect": float(RVC_PROTECT),
        "rms_mix_rate": float(RVC_RMS_MIX_RATE),
        "f0_method": str(RVC_F0_METHOD),
        "resample_sr": int(RVC_RESAMPLE_SR),
    }

    # 2) 预设覆盖（请求指定的预设优先于配置里的预设）
    name = str(req.get("preset") or RVC_PRESET or "").strip().upper()
    if name:
        p = presets.get(name)
        if not p:
            print("    [预设] 没有 %s 这档，回落到面板参数" % name, flush=True)
        else:
            for k in ("index_rate", "protect", "rms_mix_rate"):
                if p.get(k) is not None:
                    out[k] = float(p[k])
            if p.get("f0_method"):
                out["f0_method"] = str(p["f0_method"])
            if p.get("resample_sr") is not None:
                out["resample_sr"] = int(p["resample_sr"])
            print("    [预设] 套用 %s —— %s" % (name, p.get("name") or ""), flush=True)
    out["preset"] = name

    # 3) 请求里显式带的参数覆盖预设
    for k, cast in (("index_rate", float), ("protect", float),
                    ("rms_mix_rate", float), ("f0_method", str),
                    ("resample_sr", int)):
        if req.get(k) is not None:
            try:
                out[k] = cast(req[k])
            except (TypeError, ValueError):
                print("    [预设] %s 取值非法，忽略：%r" % (k, req[k]), flush=True)

    # 4) 软约束校验（越界夹回 + 告警）
    for k in ("index_rate", "rms_mix_rate", "protect"):
        rng = cons.get(k)
        if rng:
            out[k] = _clamp(out[k], float(rng["min"]), float(rng["max"]), k)
    allowed = cons.get("f0_method_allowed")
    if allowed and out["f0_method"] not in allowed:
        fb = cons.get("f0_method_default") or "rmvpe"
        print("    [预设] f0_method=%s 不在允许列表，退回 %s"
              % (out["f0_method"], fb), flush=True)
        out["f0_method"] = fb
    locked_sr = cons.get("resample_sr_allowed")
    if locked_sr is not None and out["resample_sr"] != int(locked_sr):
        out["resample_sr"] = int(locked_sr)

    # 5) 变调：pitch 只用于【预处理】（rubberband），RVC 内部变调恒为 0
    pitch = req.get("pitch") if req.get("pitch") is not None else RVC_PITCH
    try:
        out["pitch"] = int(pitch)
    except (TypeError, ValueError):
        out["pitch"] = 0
    out["separate"] = bool(req.get("separate"))
    out["harmony"] = req.get("harmony")
    return out


def _pre_pitch(src, outdir, semitones):
    """变调预处理。优先 rubberband（高质量），没有就退回 ffmpeg。

    为什么不在 RVC 内部变调（交接文档硬约束）：
      RVC 的 f0_up_key 是把基频和整条谐波一起移，音高跟着"检索特征"漂的时候
      更容易出现八度误锁（跑调）。离线 rubberband 只改音高、不动时长，稳。
    返回 (处理后的路径, 错误信息)。
    """
    if not semitones:
        return src, None
    dst = os.path.join(outdir, "pitched_%+d.wav" % semitones)
    if RUBBERBAND and os.path.isfile(RUBBERBAND):
        rc, err = _run([RUBBERBAND, "-p", str(semitones), src, dst], timeout=900)
        if rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 2000:
            print("    [变调] rubberband %+d 半音" % semitones, flush=True)
            return dst, None
        print("    [变调] rubberband 失败（%s），退回 ffmpeg" % (err or "")[:110], flush=True)
    if FFMPEG:
        # 兜底：asetrate 改音高（同时改速度），再用 atempo 链把速度补回来
        rate = 44100 * (2.0 ** (semitones / 12.0))
        t = 2.0 ** (-semitones / 12.0)
        chain = "asetrate=%d,aresample=44100" % int(round(rate))
        while t < 0.5:
            chain += ",atempo=0.5"
            t /= 0.5
        while t > 2.0:
            chain += ",atempo=2.0"
            t /= 2.0
        chain += ",atempo=%.4f" % t
        rc, err = _run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                        "-af", chain, "-ar", "44100", dst], timeout=600)
        if rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 2000:
            print("    [变调] ffmpeg 兜底 %+d 半音" % semitones, flush=True)
            return dst, None
        print("    [变调] ffmpeg 兜底也失败：%s" % (err or "")[:110], flush=True)
    return src, "变调失败：rubberband 与 ffmpeg 兜底都没成"


def _dereverb_vocal(src, outdir):
    """对人声单独跑一次去混响。

    交接文档踩坑记录：rvc_sep.py 里 RVC_DEREVERB=1 那条老路径会把【目录】
    当文件传给 librosa，直接 Permission denied。
    正确姿势是单独再跑一次 rvc_sep.py，第一个参数传【文件】而不是目录：
        rvc_sep.py VR-DeEchoDeReverb <人声文件> <人声输出目录> <占位目录> wav
    返回 (去混响后的人声路径, 错误信息)。
    """
    py = os.path.join(RVC_ROOT, "runtime", "python.exe")
    script = os.path.join(RVC_ROOT, "rvc_sep.py")
    if not os.path.isfile(py) or not os.path.isfile(script):
        return None, "找不到 RVC 运行时/分轨脚本"
    dv_dir = os.path.join(outdir, "vocal_dereverb")
    dummy = os.path.join(outdir, "_dereverb_dummy")
    env = dict(os.environ, RVC_CUDA_GRAPH="0")
    env.pop("RVC_DEREVERB", None)
    print("    [去混响] VR-DeEchoDeReverb", flush=True)
    try:
        r = subprocess.run([py, script, "VR-DeEchoDeReverb", src, dv_dir, dummy, "wav"],
                           capture_output=True, timeout=1800, env=env)
    except Exception as e:
        return None, "去混响异常: %s" % str(e)[:120]
    if r.returncode != 0:
        tail = (r.stderr or b"")[-200:].decode("utf-8", "replace")
        return None, "去混响失败(rc=%s): %s" % (r.returncode, tail)
    out = _first_audio(dv_dir)
    if not out:
        return None, "去混响没产出人声"
    print("    [去混响] 完成 %.0fKB" % (os.path.getsize(out) / 1024), flush=True)
    return out, None


def _cover_direct(src_path, params, workdir):
    """走 song_pipeline.py（交接文档标准链路），绕开 Gradio WebUI。

    链路：分轨(BS-RoFormer) → 独立去混响 → 降噪压缩 → 预变调 →
          RVC 底层直调（索引自动匹配）→ EQ + 与伴奏混音 → 响度 -14 LUFS / 44.1k
    好处：不依赖 webui.py 常驻（省一个进程、少一层 base64 中转），
          参数走 rvc_presets.json 的同一套预设与约束。
    返回 (成品路径, 错误信息)。
    """
    py = os.path.join(RVC_ROOT, "runtime", "python.exe")
    script = RVC_PIPELINE
    if not os.path.isfile(py):
        return None, "找不到 RVC 运行时: %s" % py
    if not os.path.isfile(script):
        return None, "找不到流水线脚本: %s" % script

    out = os.path.join(workdir, "direct_out.wav")
    cmd = [py, script, src_path,
           "--model", RVC_MODEL,
           "--preset", (params.get("preset") or "A"),
           "--out", out,
           "--workdir", os.path.join(workdir, "_pipeline"),
           # 参数显式传下去（流水线内部还会按 constraints 校验一遍）
           "--index-rate", str(params["index_rate"]),
           "--protect", str(params["protect"]),
           "--rms-mix-rate", str(params["rms_mix_rate"]),
           "--f0-method", str(params["f0_method"])]
    if not params.get("separate"):
        cmd.append("--no-sep")
    else:
        cmd += ["--sep-model", RVC_SEP_MODEL_DIRECT]
    if params.get("pitch"):
        cmd += ["--pitch", str(params["pitch"])]
    if params.get("harmony"):
        cmd.append("--harmony")
    if not RVC_DEREBVERB:
        cmd.append("--no-dereverb")

    print("    [直调] %s" % " ".join(cmd[1:]), flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3600,
                           cwd=RVC_ROOT, env=dict(os.environ, RVC_CUDA_GRAPH="0"))
    except subprocess.TimeoutExpired:
        return None, "直调流水线超时（3600 秒）"
    except Exception as e:
        return None, "直调流水线异常: %s" % str(e)[:120]
    so = (r.stdout or b"").decode("utf-8", "replace")
    se = (r.stderr or b"").decode("utf-8", "replace")
    if so.strip():
        print("    [直调] " + so.strip().replace("\n", "\n    [直调] ")[-1200:], flush=True)
    if r.returncode != 0:
        print("    [直调] stderr: " + se[-500:], flush=True)
        return None, "直调流水线失败(rc=%s)" % r.returncode
    if not os.path.isfile(out) or os.path.getsize(out) < 2000:
        return None, "直调没产出成品"
    print("    [直调] 完成 %.0fKB / %.0f 秒"
          % (os.path.getsize(out) / 1024, time.time() - t0), flush=True)
    return out, None


def _ensure_rvc(wait=240):
    """RVC WebUI 没开就自己拉起来（用户不用管）。

    只有翻唱才需要它；GPU 上有 GPT-SoVITS 常驻，一起加载偶尔会慢，
    所以给足 240 秒，等 7899 能连通再返回。
    """
    if not RVC_API_URL:
        return False, "没配 RVC 地址"
    try:
        u = urllib.parse.urlparse(RVC_API_URL if "//" in RVC_API_URL else "http://" + RVC_API_URL)
        host, port = u.hostname or "127.0.0.1", u.port or 7899
    except Exception:
        host, port = "127.0.0.1", 7899

    if _port_open(host, port):
        return True, ""

    py = os.path.join(RVC_ROOT, "runtime", "python.exe")
    if not os.path.isfile(py):
        return False, "RVC 没开，也找不到运行时：%s" % py
    print("    [RVC] 7899 没开，正在自动启动 RVC WebUI（首次约需 1 分钟）…", flush=True)
    env = dict(os.environ)
    env["GRADIO_ANALYTICS_ENABLED"] = "False"
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["PATH"] = os.path.join(RVC_ROOT, "runtime") + os.pathsep + env.get("PATH", "")
    try:
        flags = 0
        if os.name == "nt":
            flags = 0x08000000 | 0x00000008      # CREATE_NO_WINDOW | DETACHED_PROCESS
        subprocess.Popen([py, "-I", "webui.py", "--pycmd", py, "--port", str(port)],
                         cwd=RVC_ROOT, env=env, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
    except Exception as e:
        return False, "启动 RVC 失败：%s: %s" % (type(e).__name__, str(e)[:120])

    t0 = time.time()
    while time.time() - t0 < wait:
        if _port_open(host, port):
            print("    [RVC] 已就绪（等了 %.0f 秒）" % (time.time() - t0), flush=True)
            time.sleep(3)          # 端口开了还要几秒 gradio 才真正接请求
            return True, ""
        time.sleep(3)
    return False, "RVC 启动超时（%d 秒还没起来）" % wait


def _first_audio(folder):
    """目录里第一个音频文件（分轨脚本的输出文件名由 RVC 内部决定）。"""
    if not os.path.isdir(folder):
        return None
    for n in sorted(os.listdir(folder)):
        if n.lower().endswith((".wav", ".flac", ".mp3", ".ogg")):
            p = os.path.join(folder, n)
            if os.path.getsize(p) > 2000:
                return p
    return None


def _loud_db(path, timeout=180):
    """测一轨的平均电平（dB）。用来判断和声轨到底有没有东西。"""
    if not FFMPEG or not os.path.isfile(path):
        return -120.0
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-i", path,
                            "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, timeout=timeout)
        err = (r.stderr or b"").decode("utf-8", "replace")
    except Exception:
        return -120.0
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", err)
    return float(m.group(1)) if m else -120.0


def _separate(src_wav, outdir, timeout=900, model=None):
    """整首歌 → (人声, 伴奏)。

    走 rvc_sep.py 直调 AudioPre，不用 WebUI 的 uvr_convert
    （那个接口是生成器，gradio 3.14 同步 /api/ 只拿到第一段提示文本）。
    """
    py = os.path.join(RVC_ROOT, "runtime", "python.exe")
    script = os.path.join(RVC_ROOT, "rvc_sep.py")
    if not os.path.isfile(py):
        return None, None, "找不到 RVC 运行时: %s" % py
    if not os.path.isfile(script):
        return None, None, "找不到分轨脚本: %s" % script

    vdir = os.path.join(outdir, "vocals")
    idir = os.path.join(outdir, "inst")
    mdl = model or RVC_SEP_MODEL
    print("    [分轨] %s ← %s" % (mdl, os.path.basename(src_wav)), flush=True)
    rc, err = _run([py, script, mdl, src_wav, vdir, idir, "wav"], timeout)
    if rc != 0:
        return None, None, "分轨失败(rc=%s): %s" % (rc, (err or "")[-250:])

    vocal = _first_audio(vdir)
    inst = _first_audio(idir)
    if not vocal:
        return None, None, "分轨没产出人声"
    print("    [分轨] 人声 %.0fKB / 伴奏 %s"
          % (os.path.getsize(vocal) / 1024,
             "%.0fKB" % (os.path.getsize(inst) / 1024) if inst else "无"), flush=True)
    return vocal, inst, None


def _mix(vocal, inst, dst, gain=None, harmony=None, harmony_gain=None):
    """换好音色的人声 + 原伴奏混回去。

    注意三点：
    1) ffmpeg 是 4.3.2，amix 还没有 normalize 选项（4.4 才有），且会按输入数
       自动平均，所以先把人声 ×2、伴奏 ×(2*gain)，平均后正好是 人声 1.0 + 伴奏 gain。
    2) 旧版用 alimiter=limit=0.80 + volume=0.92 硬压，实测成品 max_volume 顶到
       0.0 dB（削波），音乐发闷有压缩感。改成 loudnorm 做响度标准化
       （目标 -14 LUFS / 真峰值 -1 dBTP），alimiter 只当兜底。
    3) 不再强制 -ac 1。伴奏是立体声，压成单声道会丢掉全部空间感；
       发语音时反正会被 tencent_silk 转单声道，但发文件时能保住立体声。
    """
    if not inst:
        return vocal
    g = RVC_INST_GAIN if gain is None else gain
    hg = RVC_HARMONY_GAIN if harmony_gain is None else harmony_gain
    n = 3 if harmony else 2      # amix 按输入数平均，所以每路先乘 n
    eq = (VOCAL_EQ + ",") if VOCAL_EQ else ""
    # 人声轨补高频（RVC 输出高频只剩原人声的 1/3，听感发闷）
    fc = ("[0:a]%svolume=%.2f,aformat=channel_layouts=stereo[v];"
          "[1:a]volume=%.2f,aformat=channel_layouts=stereo[i];"
          % (eq, 1.0 * n, n * g))
    ins = "[v][i]"
    if harmony:
        # 和声也是 RVC 出来的，同样缺高频，一起补
        fc += "[2:a]%svolume=%.2f,aformat=channel_layouts=stereo[h];" % (
            eq, n * hg)
        ins = "[v][i][h]"
    fc += ("%samix=inputs=%d:duration=longest:dropout_transition=0,"
           "alimiter=limit=0.98[a]" % (ins, n))
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", vocal, "-i", inst]
    if harmony:
        cmd += ["-i", harmony]

    # 先混出中间文件，再单独做响度处理：
    # loudnorm 默认（dynamic）会逐帧调增益，音乐上就是抽吸/起伏的"杂音"；
    # linear 模式要先拿到 measured_* 才能用，所以必须两遍。
    pre = dst if MIX_LOUDNORM == "off" else (dst + ".pre.wav")
    rc, err = _run(cmd + ["-filter_complex", fc, "-map", "[a]",
                          "-ar", "44100", pre], timeout=300)
    if rc != 0 or not os.path.exists(pre) or os.path.getsize(pre) < 2000:
        print("    [混音] 失败，只回人声: %s" % (err or "")[-200:], flush=True)
        return vocal

    if MIX_LOUDNORM != "off":
        nf = _loudnorm_filter(pre)
        if nf:
            rc, err = _run([FFMPEG, "-y", "-loglevel", "error", "-i", pre,
                            "-af", nf, "-ar", "44100", dst], timeout=300)
            if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 2000:
                print("    [混音] 响度处理失败，用未归一版本: %s"
                      % (err or "")[-160:], flush=True)
                dst = pre
        else:
            dst = pre

    print("    [混音] 完成 %.0fKB" % (os.path.getsize(dst) / 1024), flush=True)
    return dst


def _loudnorm_filter(path):
    """构造 loudnorm 滤镜串。

    - off     ：完全不管响度（混音电平本来就来自同一首歌，其实很接近）
    - linear  ：两遍法，先用 print_format=json 测出 measured_*，再整段施加
                一个恒定增益。无逐帧调制，不会有抽吸感。
    - dynamic ：ffmpeg 默认，逐帧动态增益，会压缩动态并引入 pumping，
                对已经母带过的音乐是负优化。
    """
    if MIX_LOUDNORM == "linear":
        m = _measure_loudness(path)
        if m:
            return ("loudnorm=I=%.1f:TP=%.1f:LRA=11:linear=true:"
                    "measured_I=%s:measured_LRA=%s:measured_TP=%s:"
                    "measured_thresh=%s"
                    % (MIX_TARGET_LUFS, OUT_TP, m["I"], m["LRA"],
                       m["TP"], m["thresh"]))
        print("    [混音] 响度测量失败，退回 dynamic", flush=True)
    return "loudnorm=I=%.1f:TP=%.1f:LRA=11" % (MIX_TARGET_LUFS, OUT_TP)


def _measure_loudness(path):
    """跑一遍 loudnorm 分析（不产出音频），拿 measured_* 参数。"""
    try:
        # 不能用 _run()：它只回 stderr 最后 400 字符，会把这段 json 截断
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", path,
             "-af", "loudnorm=I=%.1f:TP=%.1f:LRA=11:print_format=json"
                    % (MIX_TARGET_LUFS, OUT_TP),
             "-f", "null", "-"], capture_output=True, timeout=300)
        txt = (r.stderr or b"").decode("utf-8", "replace")
        i = txt.rfind("{")
        if i < 0:
            return None
        j = txt.rfind("}")
        d = json.loads(txt[i:j + 1])
        return {"I": d["input_i"], "LRA": d["input_lra"],
                "TP": d["input_tp"], "thresh": d["input_thresh"]}
    except Exception as e:
        print("    [混音] 测量异常: %s" % str(e)[:100], flush=True)
        return None


def _probe_stdout(cmd, timeout=30):
    """跑一个命令并取【stdout】文本。

    注意：_run() 只回 stderr 最后 400 字符，而 ffprobe 的结果在 stdout 上，
    用 _run 读 ffprobe 永远读到空（踩过这个坑）。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def _sr_of(path):
    """取音频采样率；取不到返回 0。"""
    if not FFPROBE:
        return 0
    try:
        txt = _probe_stdout([FFPROBE, "-v", "error", "-show_entries",
                             "stream=sample_rate", "-of", "default=nw=1:nk=1", path])
        v = txt.strip().splitlines()
        return int(v[-1]) if v else 0
    except Exception:
        return 0


def _ensure_44k(path, tmpd):
    """成品统一到 44.1k（交接文档的规范成品规格）。

    RVC 原生输出是 40k；两条后端出来的规格要一致，否则同一个歌从不同入口
    发出来的音质/体积会不一样。
    """
    sr = _sr_of(path)
    if not sr or sr == 44100 or not FFMPEG:
        return path
    dst = os.path.join(tmpd, "out44k.wav")
    rc, err = _run([FFMPEG, "-y", "-loglevel", "error", "-i", path,
                    "-ar", "44100", dst], timeout=300)
    if rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 2000:
        print("    [输出] 重采样到 44.1k（%d → 44100）" % sr, flush=True)
        return dst
    print("    [输出] 重采样失败，保持 %d Hz" % sr, flush=True)
    return path


COVER_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "cover_history.jsonl")
COVER_HISTORY_KEEP = 50      # 只留最近 50 条，面板读它渲染"最近翻唱"
_COVER_META = {}             # 本次任务的上下文（预设/参数/时长），由 _cover_run 填


def _record_cover(meta, out, err, cost):
    """把一次翻唱的结果追加到 cover_history.jsonl（面板「最近翻唱」读这个）。

    写失败绝不抛异常 —— 记录日志不能把正事搞挂。
    """
    if not meta.get("started"):
        return
    row = {
        "ts": round(meta.get("started") or time.time(), 1),
        "ok": bool(out),
        "cost": round(cost, 1),
        "seconds": round(meta.get("seconds") or 0, 1),
        "in_kb": int(meta.get("in_kb") or 0),
        "out_kb": int(len(base64.b64decode(out)) / 1024) if out else 0,
        "preset": meta.get("preset") or "",
        "backend": meta.get("backend") or "",
        "model": meta.get("model") or "",
        "index_rate": meta.get("index_rate"),
        "protect": meta.get("protect"),
        "rms_mix_rate": meta.get("rms_mix_rate"),
        "f0_method": meta.get("f0_method"),
        "pitch": meta.get("pitch"),
        "separate": bool(meta.get("separate")),
        "harmony": bool(meta.get("harmony")),
        "error": (str(err)[:180] if err else ""),
    }
    try:
        with open(COVER_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # 超量就裁剪（低频操作，直接重写整文件足够）
        with open(COVER_HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = [n for n in f.read().splitlines() if n.strip()]
        if len(lines) > COVER_HISTORY_KEEP:
            with open(COVER_HISTORY_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[-COVER_HISTORY_KEEP:]) + "\n")
    except Exception as e:
        print("    [记录] 翻唱历史写入失败（忽略）: %s" % str(e)[:80], flush=True)


def _finish_cover(out_path, tmpd):
    """成品文件 → base64（统一 44.1k，长音频顺手转 mp3）。返回 (b64, error)。"""
    out_path = _ensure_44k(out_path, tmpd)
    with open(out_path, "rb") as f:
        data = f.read()
    if len(data) < 2000:
        return None, "输出音频过小（%d 字节）" % len(data)

    # 长音频（整首歌）转 mp3：wav 20MB+ 传回去 QQ 发不动
    # 128k 单声道对音乐太糊（实测发闷），256k 立体声是"体积 vs 鼓点清晰度"的甜点
    # 注意别写 > 60：正好 60 秒的片段会漏判，wav 直接发到群里（10MB vs 1.9MB）
    if _wav_seconds(out_path) >= 45 and FFMPEG:
        mp3 = os.path.join(tmpd, "out.mp3")
        rc, err = _run([FFMPEG, "-y", "-loglevel", "error", "-i", out_path,
                        "-b:a", "%dk" % OUT_MP3_KBPS, mp3], timeout=300)
        if rc == 0 and os.path.exists(mp3) and os.path.getsize(mp3) > 2000:
            with open(mp3, "rb") as f:
                data = f.read()
            print("    [输出] 转 mp3 %.0fKB" % (len(data) / 1024), flush=True)

    return base64.b64encode(data).decode(), None


def do_cover(audio_b64, params=None):
    """输入音频 → 输出音频。返回 (b64, error)。

    外面这层只负责计时与写翻唱历史，真正干活的是 _cover_run。
    """
    global _COVER_META
    _COVER_META = {"started": time.time(),
                   "backend": RVC_BACKEND,
                   "model": RVC_MODEL}
    t0 = time.time()
    out, err = None, None
    try:
        out, err = _cover_run(audio_b64, params)
        return out, err
    finally:
        _record_cover(_COVER_META, out, err, time.time() - t0)


def _cover_run(audio_b64, params=None):
    """真正的翻唱实现。返回 (b64, error)"""
    params = params or {}
    if not audio_b64:
        return None, "没有收到音频数据"

    # 每次任务都重读一遍 config.json：面板上改参数不用重启客户端
    try:
        _load_config()
    except Exception:
        pass
    _INT_KEYS = ("OUT_MP3_KBPS",)
    for _k, _v in (("out_tp", "OUT_TP"), ("out_kbps", "OUT_MP3_KBPS"),
                   ("harmony_min_db", "HARMONY_MIN_DB")):
        if params.get(_k) is not None:
            try:
                globals()[_v] = (int if _v in _INT_KEYS else float)(params[_k])
            except (TypeError, ValueError):
                pass

    # RVC 推理参数统一在这里合成：面板默认 → 预设(rvc_presets.json) → 请求参数，
    # 再按 constraints 段做软约束校验。不再让请求参数直接覆盖全局变量。
    rvcp = _resolve_rvc_params(params)
    print("    [RVC] index_rate=%.2f protect=%.2f rms=%.2f f0=%s 变调=%+d(%s)"
          % (rvcp["index_rate"], rvcp["protect"], rvcp["rms_mix_rate"],
             rvcp["f0_method"], rvcp["pitch"], RVC_PITCH_MODE), flush=True)
    # 供翻唱历史记录使用（面板「最近翻唱」展示实际生效的参数）
    _COVER_META.update({
        "preset": rvcp.get("preset") or "",
        "index_rate": rvcp.get("index_rate"),
        "protect": rvcp.get("protect"),
        "rms_mix_rate": rvcp.get("rms_mix_rate"),
        "f0_method": rvcp.get("f0_method"),
        "pitch": rvcp.get("pitch"),
        "separate": bool(rvcp.get("separate")),
        "harmony": bool(rvcp.get("harmony")) if rvcp.get("harmony") is not None
                   else bool(RVC_HARMONY),
    })

    # mock 只验证链路：原样返回，不碰 ffmpeg / RVC
    if RVC_BACKEND == "mock":
        raw = base64.b64decode(audio_b64)
        print("    [mock] 原样返回 %d 字节（未做任何转换）" % len(raw), flush=True)
        return audio_b64, None

    tmpd = tempfile.mkdtemp(prefix="cover_")
    try:
        raw = base64.b64decode(audio_b64)
        ext = _guess_ext(raw)
        # 注意别和规范化输出同名，否则 ffmpeg 会拒绝写（same as Input #0）
        src = os.path.join(tmpd, "src" + ext)
        with open(src, "wb") as f:
            f.write(raw)

        wav = os.path.join(tmpd, "in.wav")
        use_path = src
        if not FFMPEG:
            print("    [警告] 没找到 ffmpeg，跳过音频规范化，直接把原文件交给 RVC", flush=True)
        else:
            ok, err = _ensure_wav(src, wav, COVER_MAX_SECONDS)
            if ok:
                use_path = wav
            else:
                print("    [警告] 规范化失败，改用原文件: %s" % (err or "")[:120], flush=True)

        secs = _wav_seconds(use_path)
        print("    输入音频: %s / %.1f 秒 / %.0f KB"
              % (os.path.basename(use_path), secs,
                 os.path.getsize(use_path) / 1024), flush=True)
        _COVER_META.update({"seconds": secs,
                            "in_kb": int(os.path.getsize(use_path) / 1024)})

        # ---- direct 后端：整条链路交给 song_pipeline.py（交接文档标准流程）----
        if RVC_BACKEND == "direct":
            out_path, e = _cover_direct(use_path, rvcp, tmpd)
            if not out_path:
                return None, e
            return _finish_cover(out_path, tmpd)

        # 整首歌必须先拆人声，否则伴奏也会被"换嗓子"，效果很怪
        inst = None
        harmony = None
        if rvcp.get("separate"):
            # RVC WebUI 没开就先拉起来（用户不用手动开）
            ok_rvc, rvc_err = _ensure_rvc()
            if not ok_rvc:
                return None, rvc_err
            vocal, inst, e = _separate(use_path, tmpd)
            if not vocal:
                return None, e or "人声分离失败"

            # 去混响：用交接文档验证过的调用方式（第一参数传文件，不是目录）
            if RVC_DEREBVERB:
                dv, de = _dereverb_vocal(vocal, tmpd)
                if dv:
                    vocal = dv
                else:
                    print("    [去混响] 失败，继续用原人声: %s" % (de or "")[:110], flush=True)

            # 和声：only_main_vocal 把和声留在伴奏轨里，那还是原唱的嗓子，
            # 跟洛天依主唱叠在一起很违和。再拆一遍把和声单独拎出来，一起换嗓子。
            want_h = params.get("harmony")
            want_h = RVC_HARMONY if want_h is None else bool(want_h)
            if want_h and inst:
                hv, hi, e2 = _separate(inst, os.path.join(tmpd, "h2"),
                                       model=RVC_SEP_MODEL2)
                if hv and hi:
                    db = _loud_db(hv)
                    if db < HARMONY_MIN_DB:
                        # 这首歌本来就没和声（或轻到听不见），别白跑一趟
                        print("    [和声] 电平 %.1f dB，判定无和声，跳过" % db, flush=True)
                        inst = hi
                    else:
                        print("    [和声] 拆出和声轨 %.1f dB" % db, flush=True)
                        harmony, inst = hv, hi
                else:
                    print("    [和声] 二次分离没结果，按原伴奏走: %s"
                          % (e2 or "")[:100], flush=True)
            use_path = vocal
        else:
            ok_rvc, rvc_err = _ensure_rvc()
            if not ok_rvc:
                return None, rvc_err

        # 变调预处理（交接文档硬约束：RVC 内部变调恒 0，升降调走 rubberband）
        if rvcp["pitch"] and RVC_PITCH_MODE == "pre":
            shifted, pe = _pre_pitch(use_path, tmpd, rvcp["pitch"])
            if pe:
                return None, pe
            use_path = shifted
            rvcp["pitch"] = 0          # 后面传给 RVC 的必须是 0

        out_path, e = _cover_gradio(use_path, rvcp)
        if not out_path:
            return None, e

        if harmony:
            h_path, e3 = _cover_gradio(harmony, rvcp)
            if h_path:
                harmony = h_path
                print("    [和声] 已换成洛天依", flush=True)
            else:
                print("    [和声] 换声失败，保留原和声: %s" % (e3 or "")[:100], flush=True)
                harmony = None

        if inst:
            out_path = _mix(out_path, inst, os.path.join(tmpd, "mixed.wav"),
                            harmony=harmony)

        return _finish_cover(out_path, tmpd)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:150])
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def main():
    _load_config()
    print("=" * 62)
    print(" 本地 TTS 客户端（GPT-SoVITS → 大肥鱼）")
    print("=" * 62)
    print("服务器  :", SERVER_URL)
    if SERVER_URL_LOCAL:
        print("本地隧道:", SERVER_URL_LOCAL, "（优先）")
    print("本机 API:", GPT_API)
    print("翻唱后端:", RVC_BACKEND,
          "（%s，模型 %s）" % (RVC_API_URL, RVC_MODEL) if RVC_BACKEND != "mock" else "（联调用，不转换）")
    print()
    missing = [n for n, v in [("GPT_MODEL_PATH", GPT_MODEL_PATH),
                              ("GPT_CKPT_PATH", GPT_CKPT_PATH),
                              ("REF_AUDIO", REF_AUDIO),
                              ("SERVER_TOKEN", SERVER_TOKEN)] if not v]
    if missing:
        print("!! 配置未填完整：", ", ".join(missing))
        print("   （SERVER_TOKEN 为空时若服务器也未设 token 也能连）")
    print()
    if not _ensure_api():
        print("!! GPT-SoVITS 不可用，客户端仍会运行，但合成会失败并被服务器回落。")
        print("   （修正后重启本客户端即可，无需重开面板）")
    print()
    print("→ 加载 GPT-SoVITS 权重…")
    how, ok_gpt, ok_sovits = _try_set_weights()
    if ok_sovits:
        print("  [OK] 权重加载成功（%s）" % how)
        if not ok_gpt:
            print("  [提示] GPT 权重未加载（可能架构不兼容），改用整合包自带的官方权重…")
            fb_gpt, _fb_sovits = _find_fallback_weights()
            if fb_gpt:
                try:
                    st, raw = _http_get(GPT_API.rstrip("/") + "/set_gpt_weights",
                                        {"weights_path": os.path.abspath(fb_gpt)})
                    if st == 200:
                        print("  [OK] 已切到官方 GPT 权重：%s" % fb_gpt)
                    else:
                        print("  [--] 官方 GPT 权重也失败：HTTP %s %s" % (st, raw[:110]))
                except Exception as e:
                    print("  [--] 官方 GPT 权重失败：%s: %s" % (type(e).__name__, str(e)[:90]))
    else:
        print("!! 权重加载失败。常见原因：")
        print("   1) GPT-SoVITS 的 api_v2.py 还没启动（检查 %s）" % GPT_API)
        print("   2) 权重路径不对（上面各方式的 HTTP 返回里会写它要什么）")
        print("   3) 你的权重是 V2 架构，而装的是 v2Pro/v5 → 需要用对应版本的整合包")
        fb_gpt, fb_sovits = _find_fallback_weights()
        if fb_gpt or fb_sovits:
            print("   提示：在整合包里找到自带权重，可把面板里的路径改成：")
            if fb_gpt:
                print("       GPT    : %s" % fb_gpt)
            if fb_sovits:
                print("       SoVITS : %s" % fb_sovits)
        print("   继续轮询（合成时可能会失败并回报给服务器 → 自动回落）")
    print()
    # 预热：先合成一句极短文本，把 GPU kernel 烤热。
    # 不做的话，空闲后第一句真请求要 8~13 秒，会超服务器超时被判 408 → 回落 Edge。
    try:
        _tw = time.time()
        _warm = synth("预热")
        if _warm:
            print("  预热完成（%.1f 秒），首句不再冷启动" % (time.time() - _tw))
        else:
            print("  预热未返回音频（不影响后续）")
    except Exception as _we:
        print("  预热失败（不影响后续）: %s" % type(_we).__name__)

    print("→ 已连上服务器，等待任务…（Ctrl+C 退出）")
    print("=" * 62)
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    while True:
        try:
            task = poll()
        except Exception as e:
            _log("[断线] %s: %s，2 秒后重连" % (type(e).__name__, str(e)[:80]))
            time.sleep(2)
            _base(force=True)     # 隧道可能断了/恢复了，重新选路上车
            continue
        tid = task.get("task_id")
        if not tid:
            continue

        # 过期任务别做：超时后服务器已经回落了，再跑只是白占 GPU
        _dl = task.get("deadline")
        if _dl:
            try:
                if time.time() > float(_dl):
                    _log("[跳过] 任务已过期，直接丢弃")
                    try:
                        post_result(tid, error="expired")
                    except Exception:
                        pass
                    continue
            except (TypeError, ValueError):
                pass

        kind = (task.get("kind") or "tts").lower()
        _t0 = time.time()
        _busy_set(kind)      # 干活期间：写 .busy + 心跳保鲜，别让看门狗误杀

        if kind == "cover":
            _log("[翻唱任务] 收到音频任务，开始处理")
            try:
                audio, err = do_cover(task.get("audio_b64") or "",
                                      task.get("params") or {})
                if audio:
                    post_result(tid, audio_b64=audio)
                    print("  → 已回传 (%.0f KB, 耗时 %.1f 秒)"
                          % (len(audio) * 3 / 4 / 1024, time.time() - _t0), flush=True)
                else:
                    post_result(tid, error=str(err)[:180])
                    print("  → 翻唱失败: %s" % err, flush=True)
            except Exception as e:
                try:
                    post_result(tid, error="%s: %s" % (type(e).__name__, str(e)[:100]))
                except Exception:
                    pass
                print("  → 异常 %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
            finally:
                _busy_clear()
            continue

        text = task.get("text", "")
        _log("[任务] %s" % text[:40])
        try:
            audio = synth(text)
            if audio:
                post_result(tid, audio_b64=audio)
                print("  → 已回传 (%.0f KB)" % (len(audio) * 3 / 4 / 1024), flush=True)
            else:
                post_result(tid, error="gpt-sovits synth failed")
                print("  → 合成失败，已回报服务器（将回落 MiMo）", flush=True)
        except Exception as e:
            try:
                post_result(tid, error="%s: %s" % (type(e).__name__, str(e)[:100]))
            except Exception:
                pass
            print("  → 异常 %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
        finally:
            _busy_clear()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出")
