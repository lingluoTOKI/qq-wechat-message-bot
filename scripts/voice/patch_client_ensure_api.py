#!/usr/bin/env python3
"""给本地客户端加「自动拉起 GPT-SoVITS」能力。

动机：面板的「启动客户端」按钮只会启动 local_tts_client.py。
电脑重启后 GPT-SoVITS 的 api_v2.py 没在跑，客户端会卡在加载权重失败，
用户以为坏了。把这段逻辑放进客户端本身，**现有面板不用改就能用**。
"""
import py_compile
import re
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
src = P.read_text(encoding="utf-8")

# 1) import socket / subprocess
if "\nimport socket\n" not in src:
    src = src.replace("import os\nimport sys\n", "import os\nimport socket\nimport subprocess\nimport sys\n", 1)

# 2) 新增 GSV_ROOT 常量
old = '# 2) 本机 GPT-SoVITS 的 API\nGPT_API = "http://127.0.0.1:9880"\n'
new = ('# 2) 本机 GPT-SoVITS 的 API\n'
       'GPT_API = "http://127.0.0.1:9880"\n'
       '# GPT-SoVITS 安装目录（客户端会在 API 没起来时自动拉起它）\n'
       'GSV_ROOT = r"F:\\github\\GPT-SoVITS-v2pro-20250604"\n')
assert old in src, "GPT_API 常量未匹配"
src = src.replace(old, new, 1)

# 3) _load_config 读 gsv_root
old = """    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH
    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG
"""
new = """    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH
    global REF_AUDIO, PROMPT_TEXT, PROMPT_LANG, GSV_ROOT
"""
assert old in src
src = src.replace(old, new, 1)

old = """    GPT_API = c.get("gpt_api") or GPT_API
"""
new = """    GPT_API = c.get("gpt_api") or GPT_API
    GSV_ROOT = c.get("gsv_root") or GSV_ROOT
"""
assert old in src
src = src.replace(old, new, 1)

# 4) 插入 _port_open / _api_addr / _ensure_api（放在 _req 之前）
anchor = "def _req(url, body=None, method=None, headers=None, timeout=20):"
assert anchor in src
helpers = '''def _port_open(host, port, timeout=1.0):
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


'''
src = src.replace(anchor, helpers + anchor, 1)

# 5) main() 里在加载权重前先确保 API
old = '''    print()
    print("→ 加载 GPT-SoVITS 权重…")'''
new = '''    print()
    if not _ensure_api():
        print("!! GPT-SoVITS 不可用，客户端仍会运行，但合成会失败并被服务器回落。")
        print("   （修正后重启本客户端即可，无需重开面板）")
    print()
    print("→ 加载 GPT-SoVITS 权重…")'''
assert old in src, "main 加载权重处未匹配"
src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("local_tts_client.py 已更新，语法通过")
for kw in ("import socket", "import subprocess", "GSV_ROOT", "_ensure_api", "_port_open"):
    print("  %-22s %d 次" % (kw, len(re.findall(re.escape(kw), src))))
