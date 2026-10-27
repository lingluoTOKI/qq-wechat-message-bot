"""给本机客户端加「音频转换（cover）」能力。

任务分发
--------
服务器下发的任务带 `kind` 字段（缺省视为 tts，兼容老任务）：
  kind="tts"   → 走 GPT-SoVITS 合成（原有逻辑）
  kind="cover" → 走 RVC 换音色（新增）

超时保护
--------
任务带 `deadline`。领到已过期的任务**直接回报错并丢弃**，不要浪费几十秒 GPU。

后端可切换（关键：先能联调，再换真后端）
----------------------------------------
  rvc_backend = "mock"    → 原样返回输入音频（用于验证整条链路，md5 可对照）
  rvc_backend = "gradio"  → 连 RVC WebUI 的 Gradio 接口（自动探测端点名）
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
bak = "%s.bak-cover-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

assert "do_cover" not in s, "已打过 cover 补丁"

# ---------- 1) import ----------
# subprocess 原文件已有，这里只补缺的 shutil / tempfile
old_imp = "import base64\n"
assert old_imp in s, "import 段未匹配"
s = s.replace(old_imp, "import base64\nimport shutil\nimport tempfile\n", 1)

# ---------- 2) 默认配置项 ----------
anchor = 'GSV_ROOT = r""'
if anchor not in s:
    # 找不到就用 CONFIG_PATH 定义处作锚点
    anchor = "CONFIG_PATH = "
    assert anchor in s
add_cfg = '''# ---- 音频转换（RVC 翻唱）----
# backend: mock = 原样返回（联调用）；gradio = 连 RVC WebUI
RVC_BACKEND = "mock"
RVC_API_URL = "http://127.0.0.1:7865/"
RVC_MODEL = "LuoTianyi-test.pth"
RVC_INDEX = ""
RVC_PITCH = 0            # 半音；男转女一般 +12
RVC_INDEX_RATE = 0.75    # 检索比例，0.4~0.8 之间比较稳
COVER_MAX_SECONDS = 90   # 超过就截断，避免一次跑太久
FFMPEG = "ffmpeg"

'''
i = s.find(anchor)
# 插到该行末尾换行之后
j = s.find("\n", i)
s = s[:j + 1] + add_cfg + s[j + 1:]

# ---------- 3) _load_config 读取新配置 ----------
old_g = "    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG, GSV_ROOT\n"
new_g = ("    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG, GSV_ROOT\n"
         "    global RVC_BACKEND, RVC_API_URL, RVC_MODEL, RVC_INDEX\n"
         "    global RVC_PITCH, RVC_INDEX_RATE, COVER_MAX_SECONDS, FFMPEG\n")
assert old_g in s, "global 声明未匹配"
s = s.replace(old_g, new_g, 1)

old_tail = '    PROMPT_LANG = c.get("prompt_lang") or PROMPT_LANG\n'
new_tail = '''    PROMPT_LANG = c.get("prompt_lang") or PROMPT_LANG
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
    FFMPEG = c.get("ffmpeg") or FFMPEG
'''
assert old_tail in s, "配置尾部未匹配"
s = s.replace(old_tail, new_tail, 1)

# ---------- 4) cover 实现（插在 def main 之前） ----------
cover_code = '''
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
    try:
        rc, out = _run([FFMPEG.replace("ffmpeg", "ffprobe") or "ffprobe",
                        "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], timeout=30)
        return float(out.strip().splitlines()[-1]) if out.strip() else 0.0
    except Exception:
        return 0.0


def _cover_gradio(wav_path, params):
    """连 RVC WebUI 的 Gradio 接口做换音色。

    端点名各版本不同（可能叫 /vc_single、/convert、/inference…），
    所以先 view_api() 拿到全部端点，按关键字挑一个再调用。
    出错时把可用端点打印出来，方便照着实调。
    """
    try:
        from gradio_client import Client, handle_file
    except Exception as e:
        return None, "缺 gradio_client（pip install gradio_client）: %s" % e

    try:
        cli = Client(RVC_API_URL, verbose=False)
    except Exception as e:
        return None, "连不上 %s : %s" % (RVC_API_URL, str(e)[:120])

    try:
        info = cli.view_api(return_format="dict", print_info=False)
    except Exception as e:
        return None, "读取 API 失败: %s" % str(e)[:120]

    named = (info or {}).get("named_endpoints") or {}
    unnamed = (info or {}).get("unnamed_endpoints") or {}
    cands = list(named.keys())
    print("    [RVC] 可用端点: %s" % (cands or "（无命名端点，仅 %d 个匿名）" % len(unnamed)))

    KEY = ("vc_single", "convert", "infer", "推理", "change")
    target = next((n for n in cands if any(k in n.lower() for k in KEY)), None)
    if not target:
        return None, "没有找到转换端点（可用：%s）" % (cands[:8] or "无")

    pitch = int(params.get("pitch", RVC_PITCH))
    index_rate = float(params.get("index_rate", RVC_INDEX_RATE))
    # 参数按位置拼：不同版本签名不同，尽量给常见的顺序
    args = [handle_file(wav_path), pitch]
    if RVC_MODEL:
        args.append(RVC_MODEL)
    if RVC_INDEX:
        args.append(RVC_INDEX)
    args.append(index_rate)

    try:
        out = cli.predict(*args, api_name=target)
    except Exception as e:
        return None, "调用 %s 失败: %s" % (target, str(e)[:160])

    # 返回可能是路径、dict 或 tuple
    path = None
    if isinstance(out, str):
        path = out
    elif isinstance(out, dict):
        for k in ("audio", "file", "path", "name"):
            if isinstance(out.get(k), str):
                path = out[k]
                break
    elif isinstance(out, (list, tuple)):
        for x in out:
            if isinstance(x, str) and os.path.exists(x):
                path = x
                break
            if isinstance(x, dict):
                for k in ("audio", "name", "path"):
                    if isinstance(x.get(k), str):
                        path = x[k]
                        break
    if not path or not os.path.exists(path):
        return None, "转换返回里找不到音频文件: %r" % (str(out)[:120],)
    return path, None


def do_cover(audio_b64, params=None):
    """输入音频 → 输出音频。返回 (b64, error)"""
    params = params or {}
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

        with open(out_path, "rb") as f:
            data = f.read()
        if len(data) < 2000:
            return None, "输出音频过小（%d 字节）" % len(data)
        return base64.b64encode(data).decode(), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:150])
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


'''
anchor_main = "\ndef main():"
assert anchor_main in s, "main 锚点未匹配"
s = s.replace(anchor_main, cover_code + "def main():", 1)

# ---------- 5) 主循环：按 kind 分发 + 过期丢弃 ----------
old_loop = '''        tid = task.get("task_id")
        if not tid:
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
'''
new_loop = '''        tid = task.get("task_id")
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
'''
assert old_loop in s, "主循环未匹配"
s = s.replace(old_loop, new_loop, 1)

# ---------- 6) 启动横幅提示 cover 后端 ----------
old_banner = '''    print("本机 API:", GPT_API)'''
new_banner = '''    print("本机 API:", GPT_API)
    print("翻唱后端:", RVC_BACKEND,
          "（%s，模型 %s）" % (RVC_API_URL, RVC_MODEL) if RVC_BACKEND != "mock" else "（联调用，不转换）")'''
assert old_banner in s, "横幅未匹配"
s = s.replace(old_banner, new_banner, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("客户端补丁已应用，语法通过。备份:", bak.rsplit(chr(92), 1)[-1])
for probe, label in (
        ("def do_cover", "cover 入口"),
        ("def _ensure_wav", "音频规范化"),
        ("def _cover_gradio", "gradio 后端"),
        ('kind = (task.get("kind") or "tts")', "任务分发"),
        ('task.get("deadline")', "过期丢弃"),
        ("RVC_BACKEND = \"mock\"", "默认 mock"),
        ("COVER_MAX_SECONDS", "时长上限")):
    print("  %-12s %s" % (label, "OK" if probe in s else "!! 缺失"))
