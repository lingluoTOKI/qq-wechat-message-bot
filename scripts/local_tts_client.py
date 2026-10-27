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
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ====================== 配置区（改这里或 config.json） ======================
# 1) 服务器上的代理地址（公网可达）
SERVER_URL = "https://game.lingluotoki.dpdns.org/tts"
SERVER_TOKEN = ""              # 与服务器 LTT_TOKEN 一致

# 2) 本机 GPT-SoVITS 的 API
GPT_API = "http://127.0.0.1:9880"
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


def _load_config():
    """config.json 里的值覆盖上面的默认值（管理面板会写这个文件）"""
    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH
    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            c = json.load(f)
    except Exception:
        return
    SERVER_URL = c.get("server_url") or SERVER_URL
    SERVER_TOKEN = c.get("server_token") or SERVER_TOKEN
    GPT_API = c.get("gpt_api") or GPT_API
    GPT_MODEL_PATH = c.get("gpt_model_path") or GPT_MODEL_PATH
    GPT_CKPT_PATH = c.get("gpt_ckpt_path") or GPT_CKPT_PATH
    REF_AUDIO = c.get("ref_audio") or REF_AUDIO
    PROMPT_TEXT = c.get("prompt_text") or PROMPT_TEXT
    PROMPT_LANG = c.get("prompt_lang") or PROMPT_LANG


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


def poll():
    url = SERVER_URL.rstrip("/") + "/poll"
    body = json.dumps({}).encode()
    h = {"Content-Type": "application/json"}
    if SERVER_TOKEN:
        h["X-TTSToken"] = SERVER_TOKEN
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def post_result(task_id, audio_b64=None, error=None):
    url = SERVER_URL.rstrip("/") + "/result"
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


def main():
    _load_config()
    print("=" * 62)
    print(" 本地 TTS 客户端（GPT-SoVITS → 大肥鱼）")
    print("=" * 62)
    print("服务器  :", SERVER_URL)
    print("本机 API:", GPT_API)
    print()
    missing = [n for n, v in [("GPT_MODEL_PATH", GPT_MODEL_PATH),
                              ("GPT_CKPT_PATH", GPT_CKPT_PATH),
                              ("REF_AUDIO", REF_AUDIO),
                              ("SERVER_TOKEN", SERVER_TOKEN)] if not v]
    if missing:
        print("!! 配置未填完整：", ", ".join(missing))
        print("   （SERVER_TOKEN 为空时若服务器也未设 token 也能连）")
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
    print("→ 已连上服务器，等待任务…（Ctrl+C 退出）")
    print("=" * 62)
    while True:
        try:
            task = poll()
        except Exception as e:
            print("[断线] %s: %s，5 秒后重连" % (type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(5)
            continue
        tid = task.get("task_id")
        if not tid:
            continue
        text = task.get("text", "")
        print("[任务] %s" % text[:40], flush=True)
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出")
