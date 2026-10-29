#!/usr/bin/env python3
"""改造 panel_server.py：面板一键启动 = 先拉 GPT-SoVITS API，再拉客户端。

为什么需要改：
原来的 /local/tts/start 只启动 local_tts_client.py。电脑重启后 GPT-SoVITS
的 api_v2.py 并没在跑，客户端会卡在"加载权重"然后一直失败，用户以为坏了。

改法：
1. 新增 _port_open 探测 9880
2. 新增 _gsv_start()：按 config 里的 gsv_root 拉起 api_v2.py，记 pid
3. /local/tts/start 改为异步：后台线程「等 API 就绪 -> 再起客户端」，
   立即返回 state=starting_*，页面轮询 /status 看进度（避免 HTTP 卡 3 分钟）
4. /local/tts/stop 一并停掉我们拉起的 API，释放显存
5. status 增加 api_ready / api_pid / launch 进度
"""
import py_compile
import sys
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\panel_server.py")
src = P.read_text(encoding="utf-8")
orig = src

# ---------- 1. 常量 ----------
old = "TTS_SERVER = 'https://game.lingluotoki.dpdns.org/tts'\n"
new = old + """
# GPT-SoVITS 本体（api_v2.py）的启动管理：面板一键启动时先把它拉起来
TTS_API_PIDFILE = LOCAL_TTS_DIR / 'gsv.pid'
TTS_API_LOG = LOCAL_TTS_DIR / 'gsv.log'
TTS_API_ERRLOG = LOCAL_TTS_DIR / 'gsv.err.log'
DEFAULT_GSV_ROOT = r'F:\\github\\GPT-SoVITS-v2pro-20250604'
TTS_LAUNCH = {'state': 'idle', 'message': ''}
TTS_LAUNCH_LOCK = threading.Lock()
"""
assert old in src and "TTS_API_PIDFILE" not in src
src = src.replace(old, new, 1)

# ---------- 2. 替换 _tts_start_client ----------
old = """def _tts_start_client():
    running, pid = _tts_client_running()
    if running:
        return {'ok': True, 'state': 'running', 'message': '客户端已在运行（PID %d）' % pid, 'pid': pid}
    if not TTS_CLIENT.exists():
        return {'ok': False, 'state': 'error', 'message': '找不到 %s' % TTS_CLIENT}
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        with TTS_LOG.open('ab') as out, TTS_ERRLOG.open('ab') as err:
            proc = subprocess.Popen([sys.executable, str(TTS_CLIENT)],
                                    cwd=str(LOCAL_TTS_DIR),
                                    creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                                    stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL)
        TTS_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        return {'ok': True, 'state': 'starting', 'message': '客户端已启动（PID %d），正在连接服务器…' % proc.pid,
                'pid': proc.pid}
    except Exception as e:
        return {'ok': False, 'state': 'error', 'message': '启动失败：%s: %s' % (type(e).__name__, str(e)[:120])}
"""

new = '''def _tts_cfg():
    try:
        return json.loads(TTS_CONFIG.read_text(encoding='utf-8-sig')) if TTS_CONFIG.exists() else {}
    except Exception:
        return {}


def _port_open(host, port, timeout=1.0):
    """探测本机端口，判断 GPT-SoVITS API 是否已在监听。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _gsv_endpoint():
    """从 config 的 gpt_api 解析出 (host, port)，默认 127.0.0.1:9880。"""
    api = str(_tts_cfg().get('gpt_api') or 'http://127.0.0.1:9880')
    try:
        p = urllib.parse.urlparse(api)
        return (p.hostname or '127.0.0.1'), int(p.port or 9880)
    except Exception:
        return '127.0.0.1', 9880


def _gsv_ready():
    host, port = _gsv_endpoint()
    return _port_open(host, port)


def _gsv_pid():
    try:
        return int(TTS_API_PIDFILE.read_text(encoding='utf-8').strip())
    except Exception:
        return 0


def _gsv_start():
    """拉起 GPT-SoVITS api_v2.py（若端口已开则直接返回）。"""
    if _gsv_ready():
        return {'ok': True, 'state': 'ready', 'message': 'GPT-SoVITS 已在运行'}
    root = Path(str(_tts_cfg().get('gsv_root') or DEFAULT_GSV_ROOT))
    exe = root / 'runtime' / 'python.exe'
    script = root / 'api_v2.py'
    yaml = root / 'GPT_SoVITS' / 'configs' / 'tts_infer.yaml'
    if not script.exists():
        # 有些整合包直接带 python.exe 在根目录
        exe = root / 'python.exe'
    for f, label in ((exe, 'python.exe'), (script, 'api_v2.py'), (yaml, 'tts_infer.yaml')):
        if not f.exists():
            return {'ok': False, 'state': 'error',
                    'message': 'GPT-SoVITS 路径不对，找不到 %s（%s）。请在配置里改 gsv_root。' % (label, f)}
    host, port = _gsv_endpoint()
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        with TTS_API_LOG.open('ab') as out, TTS_API_ERRLOG.open('ab') as err:
            proc = subprocess.Popen(
                [str(exe), str(script), '-a', host, '-p', str(port), '-c', str(yaml)],
                cwd=str(root),
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        TTS_API_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        return {'ok': True, 'state': 'starting', 'message': '正在启动 GPT-SoVITS（PID %d）…' % proc.pid}
    except Exception as e:
        return {'ok': False, 'state': 'error',
                'message': '启动 GPT-SoVITS 失败：%s: %s' % (type(e).__name__, str(e)[:140])}


def _tts_start_client_sync():
    """真正启动客户端进程（同步，内部用）。"""
    running, pid = _tts_client_running()
    if running:
        return {'ok': True, 'state': 'running', 'message': '客户端已在运行（PID %d）' % pid, 'pid': pid}
    if not TTS_CLIENT.exists():
        return {'ok': False, 'state': 'error', 'message': '找不到 %s' % TTS_CLIENT}
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        with TTS_LOG.open('ab') as out, TTS_ERRLOG.open('ab') as err:
            proc = subprocess.Popen([sys.executable, str(TTS_CLIENT)],
                                    cwd=str(LOCAL_TTS_DIR),
                                    creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                                    stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL)
        TTS_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        return {'ok': True, 'state': 'starting', 'message': '客户端已启动（PID %d），正在连接服务器…' % proc.pid,
                'pid': proc.pid}
    except Exception as e:
        return {'ok': False, 'state': 'error', 'message': '启动失败：%s: %s' % (type(e).__name__, str(e)[:120])}


def _tts_launch_worker():
    """后台线程：等 GPT-SoVITS 就绪 -> 启动客户端。"""
    try:
        TTS_LAUNCH.update(state='starting_api', message='正在启动 GPT-SoVITS…')
        r = _gsv_start()
        if not r.get('ok'):
            TTS_LAUNCH.update(state='error', message=r.get('message', '启动失败'))
            return

        if not _gsv_ready():
            TTS_LAUNCH.update(state='waiting_api',
                              message='等待 GPT-SoVITS 加载模型（首次约 30~60 秒，GPU 预热另需几秒）…')
            deadline = time.time() + 240
            while time.time() < deadline:
                if _gsv_ready():
                    break
                time.sleep(2)
            else:
                TTS_LAUNCH.update(state='error',
                                  message='GPT-SoVITS 4 分钟内没有起来，请查看 本地TTS/gsv.err.log')
                return

        TTS_LAUNCH.update(state='starting_client', message='模型就绪，正在启动客户端…')
        r = _tts_start_client_sync()
        if not r.get('ok'):
            TTS_LAUNCH.update(state='error', message=r.get('message', '启动客户端失败'))
            return
        time.sleep(3)
        online, _raw = _tts_server_online()
        TTS_LAUNCH.update(state='ready' if online else 'connecting',
                          message='已启动，正在与服务器建立连接…' if not online
                          else '已启动并连上服务器，语音将由本机合成')
    except Exception as e:
        TTS_LAUNCH.update(state='error', message='启动异常：%s' % type(e).__name__)
    finally:
        TTS_LAUNCH_LOCK.release()


def _tts_start_client():
    """对外入口：立即返回，后台线程负责「先 API 后客户端」。"""
    running, pid = _tts_client_running()
    if running and _gsv_ready():
        TTS_LAUNCH.update(state='ready', message='已在运行')
        return {'ok': True, 'state': 'running', 'message': '已在运行（PID %d）' % pid, 'pid': pid}
    if not TTS_LAUNCH_LOCK.acquire(blocking=False):
        return {'ok': True, 'state': TTS_LAUNCH['state'],
                'message': TTS_LAUNCH['message'] or '正在启动中…'}
    TTS_LAUNCH.update(state='starting_api', message='正在启动…')
    threading.Thread(target=_tts_launch_worker, daemon=True).start()
    return {'ok': True, 'state': 'starting_api',
            'message': '已开始启动：先拉起 GPT-SoVITS，再连接服务器（首次约 1 分钟）'}
'''
assert old in src
src = src.replace(old, new, 1)

# ---------- 3. stop 同时停掉我们拉起的 API ----------
old = """    try:
        TTS_PIDFILE.unlink()
    except Exception:
        pass
    return {'ok': True, 'state': 'stopped', 'message': '客户端已停止（PID %d）' % pid}"""
new = """    try:
        TTS_PIDFILE.unlink()
    except Exception:
        pass
    # 一并停掉面板拉起的 GPT-SoVITS（释放显存）；手动起的进程不受影响（无 pidfile）
    api_pid = _gsv_pid()
    api_msg = ''
    if api_pid and _tts_pid_alive(api_pid):
        try:
            subprocess.run(['taskkill', '/PID', str(api_pid), '/T', '/F'],
                           capture_output=True, text=True, errors='replace', timeout=15)
            api_msg = '，已同时停止 GPT-SoVITS（PID %d）' % api_pid
        except Exception:
            api_msg = '，但停止 GPT-SoVITS 失败'
    try:
        TTS_API_PIDFILE.unlink()
    except Exception:
        pass
    TTS_LAUNCH.update(state='idle', message='已停止')
    return {'ok': True, 'state': 'stopped',
            'message': '客户端已停止（PID %d）%s' % (pid, api_msg)}"""
assert old in src
src = src.replace(old, new, 1)

# ---------- 4. status 增加字段 ----------
old = """    return {
        'ok': True,
        'running': running,
        'pid': pid,
        'server_online': online,"""
new = """    return {
        'ok': True,
        'running': running,
        'pid': pid,
        'api_ready': _gsv_ready(),
        'api_pid': _gsv_pid(),
        'launch': dict(TTS_LAUNCH),
        'server_online': online,"""
assert old in src
src = src.replace(old, new, 1)

# ---------- 5. 允许保存 gsv_root ----------
old = """    allowed = ('server_url', 'server_token', 'gpt_api', 'gpt_model_path', 'gpt_ckpt_path',
               'ref_audio', 'prompt_text', 'prompt_lang')"""
new = """    allowed = ('server_url', 'server_token', 'gpt_api', 'gpt_model_path', 'gpt_ckpt_path',
               'ref_audio', 'prompt_text', 'prompt_lang', 'gsv_root')"""
assert old in src
src = src.replace(old, new, 1)

# ---------- 6. 补 import ----------
if "import socket" not in src.split("\n\n")[0] and "\nimport socket\n" not in src[:2000]:
    if "\nimport secrets\n" in src:
        src = src.replace("\nimport secrets\n", "\nimport secrets\nimport socket\n", 1)
    else:
        src = src.replace("\nimport subprocess\n", "\nimport socket\nimport subprocess\n", 1)
if "\nimport time\n" not in src[:2000]:
    src = src.replace("\nimport threading\n", "\nimport threading\nimport time\n", 1)
if "import urllib.parse" not in src[:2000]:
    src = src.replace("\nimport urllib.request\n", "\nimport urllib.parse\nimport urllib.request\n", 1)

if src == orig:
    print("!! 没有任何改动")
    sys.exit(1)

P.write_text(src, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("已改造 panel_server.py，语法检查通过")

# 自检
import re
for kw in ("_gsv_start", "_tts_launch_worker", "api_ready", "gsv_root", "_port_open"):
    print("  %-22s 出现 %d 次" % (kw, len(re.findall(kw, src))))
for mod in ("import socket", "import time", "import urllib.parse"):
    print("  %-22s %s" % (mod, "OK" if mod in src else "!! 缺失"))
