"""修客户端 cover 的两个问题：

① 找不到 ffmpeg（WinError 2）
   用户 PATH 里没有 ffmpeg，但 GPT-SoVITS 整合包自带
   `runtime/ffmpeg.exe` 和 `ffprobe.exe`。
   → 加自动探测：配置值 → PATH → <gsv_root>/runtime/ → 常见子目录

② mock 后端不该依赖 ffmpeg
   mock 的用途是验证链路，直接原样返回输入即可，
   不必先规范化，这样没有 ffmpeg 也能联调。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
bak = "%s.bak-ffmpeg-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

assert "_resolve_fftools" not in s, "已打过 ffmpeg 补丁"

# ---------- 1) 默认值改成空（= 自动探测），并加 FFPROBE ----------
old = 'FFMPEG = "ffmpeg"\n'
new = 'FFMPEG = ""     # 空 = 自动探测（配置 → PATH → <gsv_root>/runtime/）\nFFPROBE = ""\n'
assert old in s, "FFMPEG 默认值未匹配"
s = s.replace(old, new, 1)

# ---------- 2) 配置读取：ffprobe ----------
old_cfg = '    FFMPEG = c.get("ffmpeg") or FFMPEG\n'
new_cfg = '    FFMPEG = c.get("ffmpeg") or FFMPEG\n    FFPROBE = c.get("ffprobe") or FFPROBE\n'
assert old_cfg in s, "ffmpeg 配置项未匹配"
s = s.replace(old_cfg, new_cfg, 1)

old_glob = "    global RVC_PITCH, RVC_INDEX_RATE, COVER_MAX_SECONDS, FFMPEG\n"
new_glob = "    global RVC_PITCH, RVC_INDEX_RATE, COVER_MAX_SECONDS, FFMPEG, FFPROBE\n"
assert old_glob in s
s = s.replace(old_glob, new_glob, 1)

# 配置读完后立刻解析路径
old_tail = '    FFMPEG = c.get("ffmpeg") or FFMPEG\n    FFPROBE = c.get("ffprobe") or FFPROBE\n'
new_tail = ('    FFMPEG = c.get("ffmpeg") or FFMPEG\n'
            '    FFPROBE = c.get("ffprobe") or FFPROBE\n'
            '    _resolve_fftools()\n')
assert old_tail in s
s = s.replace(old_tail, new_tail, 1)

# ---------- 3) 探测函数（插在 _load_config 之前，供其调用） ----------
anchor = "def _load_config():"
assert anchor in s, "_load_config 锚点未匹配"

resolver = '''def _resolve_fftools():
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


'''
s = s.replace(anchor, resolver + anchor, 1)

# ---------- 4) _wav_seconds 用 FFPROBE，且没有则回退 ffmpeg 解析 ----------
old_ws = '''def _wav_seconds(path):
    try:
        rc, out = _run([FFMPEG.replace("ffmpeg", "ffprobe") or "ffprobe",
                        "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], timeout=30)
        return float(out.strip().splitlines()[-1]) if out.strip() else 0.0
    except Exception:
        return 0.0
'''
new_ws = '''def _wav_seconds(path):
    """拿音频时长；拿不到就返回 0（不做时长限制，不报错）。"""
    if FFPROBE:
        try:
            rc, out = _run([FFPROBE, "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", path],
                           timeout=30)
            v = out.strip().splitlines()
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
            m = _re.search(r"Duration:\\s*(\\d+):(\\d+):(\\d+\\.\\d+)", txt)
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
    if h[:3] == b"ID3" or h[:2] in (b"\\xff\\xfb", b"\\xff\\xf3"):
        return ".mp3"
    if h[:5] == b"#!AMR":
        return ".amr"
    if h[:4] == b"\\x02#!S":
        return ".silk"
    if h[4:8] == b"ftyp":
        return ".m4a"
    return ".bin"
'''
assert old_ws in s, "_wav_seconds 未匹配"
s = s.replace(old_ws, new_ws, 1)

# ---------- 5) do_cover：mock 直接返回 + 无 ffmpeg 优雅降级 ----------
old_dc = '''    params = params or {}
    tmpd = tempfile.mkdtemp(prefix="cover_")
    try:
        raw = base64.b64decode(audio_b64)
        src = os.path.join(tmpd, "in.bin")
        with open(src, "wb") as f:
            f.write(raw)

        wav = os.path.join(tmpd, "in.wav")
        ok, err = _ensure_wav(src, wav, COVER_MAX_SECONDS)
        if not ok:
            return None, "音频解码失败（可能不是音频）: %s" % (err or "")[:120]

        secs = _wav_seconds(wav)
        print("    输入音频: %.1f 秒 / %.0f KB" % (secs, os.path.getsize(wav) / 1024), flush=True)

        if RVC_BACKEND == "mock":
            out_path = wav          # 原样返回：md5 与输入一致即证明链路通
            print("    [mock] 跳过真实转换（rvc_backend=mock）", flush=True)
        else:
            out_path, e = _cover_gradio(wav, params)
            if not out_path:
                return None, e
'''
new_dc = '''    params = params or {}
    if not audio_b64:
        return None, "没有收到音频数据"

    # mock 只验证链路：原样返回，不碰 ffmpeg / RVC
    if RVC_BACKEND == "mock":
        raw = base64.b64decode(audio_b64)
        print("    [mock] 原样返回 %d 字节（未做任何转换）" % len(raw), flush=True)
        return audio_b64, None

    tmpd = tempfile.mkdtemp(prefix="cover_")
    try:
        raw = base64.b64decode(audio_b64)
        ext = _guess_ext(raw)
        src = os.path.join(tmpd, "in" + ext)
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

        out_path, e = _cover_gradio(use_path, params)
        if not out_path:
            return None, e
'''
assert old_dc in s, "do_cover 主体未匹配"
s = s.replace(old_dc, new_dc, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("补丁已应用，语法通过。备份:", bak.rsplit(chr(92), 1)[-1])
for probe, label in (
        ("def _resolve_fftools", "ffmpeg 探测"),
        ("def _guess_ext", "扩展名猜测"),
        ("FFPROBE", "ffprobe 支持"),
        ("_resolve_fftools()", "配置后解析"),
        ("没找到 ffmpeg", "无 ffmpeg 降级"),
        ("[mock] 原样返回", "mock 免 ffmpeg")):
    print("  %-14s %s" % (label, "OK" if probe in s else "!! 缺失"))
