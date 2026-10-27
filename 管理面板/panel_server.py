#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOT CONSOLE 本地服务
- 在 127.0.0.1 上提供管理面板页面
- 把 /api/* 代理到服务器上的面板 API（经 SSH 隧道）
- 同时充当 astrbot / snowluma / novnc 三个入口的跳转网关（带隧道连通性探测）

不依赖任何第三方库，Windows 自带 Python 即可运行。
"""
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import secrets
import subprocess
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_PORT = 8765
API_PORT = 8080          # 服务器端面板 API（经隧道映射到本地）
UPSTREAM = "http://127.0.0.1:%d" % API_PORT

# 需要透传给浏览器的入口
ENTRY_PORTS = {"astrbot": 6185, "snowluma": 5099, "novnc": 6081}
LOCAL_TOKEN = secrets.token_urlsafe(32)
REMINDER = Path(HERE).parent / '个人QQ重要消息提醒'
START_LOCK = threading.Lock()
START_STATUS = {'state':'idle','message':'尚未启动'}

# ---- 本地 TTS（GPT-SoVITS @ 本机）----
LOCAL_TTS_DIR = Path(HERE) / '本地TTS'
TTS_CLIENT = LOCAL_TTS_DIR / 'local_tts_client.py'
TTS_CONFIG = LOCAL_TTS_DIR / 'config.json'
TTS_PIDFILE = LOCAL_TTS_DIR / 'client.pid'
TTS_LOG = LOCAL_TTS_DIR / 'client.log'
TTS_ERRLOG = LOCAL_TTS_DIR / 'client.err.log'
TTS_SERVER = 'https://game.lingluotoki.dpdns.org/tts'

# GPT-SoVITS 本体（api_v2.py）的启动管理：面板一键启动时先把它拉起来
TTS_API_PIDFILE = LOCAL_TTS_DIR / 'gsv.pid'
TTS_API_LOG = LOCAL_TTS_DIR / 'gsv.log'
TTS_API_ERRLOG = LOCAL_TTS_DIR / 'gsv.err.log'
DEFAULT_GSV_ROOT = r'F:\github\GPT-SoVITS-v2pro-20250604'
TTS_LAUNCH = {'state': 'idle', 'message': ''}
TTS_LAUNCH_LOCK = threading.Lock()

# ---- 本地 RVC（翻唱引擎 @ 本机 7899）----
RVC_ROOT = Path(r'F:\github\RVC20260718Nvidia')  # 整合包根目录（webui.py 在这里）
RVC_PORT = 7899
RVC_PIDFILE = LOCAL_TTS_DIR / 'rvc.pid'
RVC_LOG = LOCAL_TTS_DIR / 'rvc.log'
RVC_ERRLOG = LOCAL_TTS_DIR / 'rvc.err.log'

# ---- 本地 GPT-SoVITS 图形面板（webui.py @ 本机 9874）----
# 注意：这与合成用的 api_v2.py（9880，由 TTS 启动拉起）是两个独立进程。
# 面板「打开面板」打开的是图形 webui（9874）；api 只是 JSON 接口，浏览器打开没有界面。
GSV_WEBUI_ROOT = Path(r'F:\github\GPT-SoVITS-v2pro-20250604')
GSV_WEBUI_PORT = 9874
GSV_WEBUI_PIDFILE = LOCAL_TTS_DIR / 'gsv_webui.pid'
GSV_WEBUI_LOG = LOCAL_TTS_DIR / 'gsv_webui.log'
GSV_WEBUI_ERRLOG = LOCAL_TTS_DIR / 'gsv_webui.err.log'

# ---- SSH 隧道保活（本机 ↔ 服务器）----
# 面板自己负责建立并看管隧道，断了自动重连，避免依赖某个临时会话。
# 转发规则与「启动管理面板.bat」保持一致：本地端口 -> 服务器 127.0.0.1:同端口
TUNNEL_HOST = '101.132.104.41'
TUNNEL_USER = 'root'
TUNNEL_KEY = os.path.expanduser('~/.ssh/id_ed25519')
TUNNEL_FORWARDS = [(6185, 6185), (5099, 5099), (6081, 6081),
                   (8080, 8080), (32000, 32000)]
TUNNEL_PIDFILE = Path(HERE) / 'tunnel.pid'
_tunnel_lock = threading.Lock()


def _ssh_exe():
    """找一个能用的 ssh 客户端，优先系统 OpenSSH。"""
    for cand in ('ssh',
                 r'C:\Windows\System32\OpenSSH\ssh.exe',
                 r'C:\Program Files\Git\usr\bin\ssh.exe'):
        try:
            r = subprocess.run([cand, '-V'], capture_output=True,
                               text=True, errors='replace', timeout=5)
            if r.returncode == 0 or 'OpenSSH' in (r.stderr or r.stdout):
                return cand
        except Exception:
            continue
    return 'ssh'


def _tunnel_alive():
    """只要 32000 在监听就认为隧道可用（客户端/RVC 走这条）。"""
    return _port_open('127.0.0.1', 32000)


def _tunnel_start():
    """建 SSH 隧道（DETACHED，断线由看门狗重连）。已存在则直接返回。"""
    if _tunnel_alive():
        return True
    ssh = _ssh_exe()
    if not os.path.exists(TUNNEL_KEY):
        print('[tunnel] 找不到 SSH 私钥: %s' % TUNNEL_KEY)
        return False
    # Popen 不经 shell，必须把每个 -L 和转发规则拆成独立参数，
    # 否则会整串当成一个参数 -> "Bad local forwarding specification"。
    cmd = [ssh, '-N', '-T',
           '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
           '-o', 'ExitOnForwardFailure=yes',
           '-o', 'StrictHostKeyChecking=accept-new',
           '-i', TUNNEL_KEY]
    for lp, rp in TUNNEL_FORWARDS:
        cmd += ['-L', '%d:127.0.0.1:%d' % (lp, rp)]
    cmd.append('%s@%s' % (TUNNEL_USER, TUNNEL_HOST))
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        logdir = Path(HERE) / 'logs'
        if not logdir.exists():
            logdir.mkdir(parents=True, exist_ok=True)
        with (logdir / 'tunnel.log').open('ab') as log:
            proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                                    stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        TUNNEL_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        # 等端口起来（最多 25 秒）
        for _ in range(25):
            if _tunnel_alive():
                print('[tunnel] 隧道已建立（PID %d）' % proc.pid)
                return True
            time.sleep(1)
        print('[tunnel] 25 秒内端口未起来，查看 logs/tunnel.log')
        return _tunnel_alive()
    except Exception as e:
        print('[tunnel] 启动失败: %s: %s' % (type(e).__name__, str(e)[:140]))
        return False


def _tunnel_watchdog():
    """后台线程：隧道掉了就重建。永远不会主动杀已有隧道。"""
    while True:
        try:
            if not _tunnel_alive():
                with _tunnel_lock:
                    if not _tunnel_alive():
                        print('[tunnel] 未检测到隧道，尝试重建…')
                        _tunnel_start()
        except Exception:
            pass
        time.sleep(30)


def _tts_pid_alive(pid):
    """Windows 下用 tasklist 判活，避免 os.kill 的权限异常误判"""
    try:
        # Windows 的 tasklist 输出是 GBK，text=True 会按 UTF-8 解码而炸掉；
        # 用 errors='replace' 兜住非法字节。
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'],
                             capture_output=True, text=True,
                             errors='replace', timeout=8).stdout
        return str(pid) in out
    except Exception:
        return False


def _tts_client_running():
    try:
        pid = int(TTS_PIDFILE.read_text(encoding='utf-8').strip())
    except Exception:
        return False, 0
    return (_tts_pid_alive(pid), pid)


def _tts_server_online():
    """问服务器：本地客户端是否已连上（直连公网 HTTPS，不走隧道）

    注意：这里**不用**短超时。实测 6 秒会在首次 TLS 握手 + 境外/公网往返时
    偶发 URLError，导致面板误报"离线"（而实际服务器已返回 online:true）。
    改为 15 秒并重试一次。
    """
    try:
        cfg = json.loads(TTS_CONFIG.read_text(encoding='utf-8-sig')) if TTS_CONFIG.exists() else {}
    except Exception:
        cfg = {}
    token = str(cfg.get('server_token') or '')
    # 通道顺序与客户端一致：本地 SSH 隧道优先，其次公网 HTTPS。
    # 原因：本机 DNS 被代理工具改成 fake-ip，该域名走直连 → TLS 会被断连，
    # 而隧道是纯 HTTP over SSH，稳定得多。
    cands = []
    local = str(cfg.get('server_url_local') or '')
    if local:
        cands.append(local)
    cands.append(str(cfg.get('server_url') or TTS_SERVER))

    last_err = None
    for base in cands:
        url = base.rstrip('/') + '/health'
        for _ in range(2):
            try:
                req = urllib.request.Request(url, headers={'X-TTSToken': token} if token else {})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode('utf-8'))
                data['via'] = '本地隧道' if base is not cands[-1] or len(cands) == 1 else '公网'
                return bool(data.get('online')), data
            except Exception as e:
                last_err = e
    return False, {'error': type(last_err).__name__, 'detail': str(last_err)[:120]}


def _tts_cfg():
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
    # 客户端/RVC 走 SSH 隧道（32000）。先确保隧道在线，否则连不上服务器。
    if not _tunnel_alive():
        _tunnel_start()
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


def _tts_stop_client():
    running, pid = _tts_client_running()
    if not running:
        try:
            TTS_PIDFILE.unlink()
        except Exception:
            pass
        return {'ok': True, 'state': 'stopped', 'message': '客户端未在运行'}
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, text=True,
                       errors='replace', timeout=15)
    except Exception:
        pass
    try:
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
            'message': '客户端已停止（PID %d）%s' % (pid, api_msg)}


def _tts_status():
    running, pid = _tts_client_running()
    online, raw = _tts_server_online()
    try:
        cfg = json.loads(TTS_CONFIG.read_text(encoding='utf-8-sig')) if TTS_CONFIG.exists() else {}
    except Exception:
        cfg = {}
    tail = ''
    if TTS_ERRLOG.exists():
        try:
            lines = TTS_ERRLOG.read_text(encoding='utf-8', errors='ignore').strip().splitlines()
            tail = '\n'.join(lines[-12:])
        except Exception:
            tail = ''
    return {
        'ok': True,
        'running': running,
        'pid': pid,
        'api_ready': _gsv_ready(),
        'api_pid': _gsv_pid(),
        'launch': dict(TTS_LAUNCH),
        'server_online': online,
        'server_raw': raw,
        'config': {k: cfg.get(k, '') for k in
                   ('server_url', 'server_url_local', 'gpt_api', 'gpt_model_path',
                    'gpt_ckpt_path', 'ref_audio', 'prompt_text', 'gsv_root')},
        'token_set': bool(cfg.get('server_token')),
        'error_log': tail,
    }


def _tts_save_config(data):
    allowed = ('server_url', 'server_token', 'gpt_api', 'gpt_model_path', 'gpt_ckpt_path',
               'ref_audio', 'prompt_text', 'prompt_lang', 'gsv_root')
    try:
        cfg = json.loads(TTS_CONFIG.read_text(encoding='utf-8-sig')) if TTS_CONFIG.exists() else {}
    except Exception:
        cfg = {}
    for k in allowed:
        v = data.get(k)
        if isinstance(v, str):
            cfg[k] = v
    TTS_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    running, _ = _tts_client_running()
    if running:
        _tts_stop_client()
        return {'ok': True, 'saved': True, 'restarted': True,
                'message': '配置已保存，客户端已重启以应用新配置'}
    return {'ok': True, 'saved': True, 'restarted': False, 'message': '配置已保存（客户端未运行，启动后生效）'}


# ---- 本地 RVC（翻唱引擎）----
def _rvc_pid_alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                             capture_output=True, text=True, errors='replace', timeout=10)
        return str(pid) in out.stdout
    except Exception:
        return False


def _rvc_ready():
    return _port_open('127.0.0.1', RVC_PORT)


def _rvc_pid():
    try:
        return int(RVC_PIDFILE.read_text(encoding='utf-8').strip())
    except Exception:
        return 0


def _rvc_start():
    """拉起 RVC webui.py（若端口已开则直接返回）。"""
    if _rvc_ready():
        return {'ok': True, 'state': 'ready', 'message': 'RVC 已在运行'}
    py = RVC_ROOT / 'runtime' / 'python.exe'
    script = RVC_ROOT / 'webui.py'
    if not py.exists():
        py = RVC_ROOT / 'python.exe'
    if not script.exists() or not py.exists():
        return {'ok': False, 'state': 'error',
                'message': '找不到 RVC 的 webui.py 或 runtime/python.exe（请确认 RVC_ROOT）'}
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        env = dict(os.environ)
        env['PATH'] = str(RVC_ROOT / 'runtime') + os.pathsep + env.get('PATH', '')
        with RVC_LOG.open('ab') as out, RVC_ERRLOG.open('ab') as err:
            proc = subprocess.Popen(
                [str(py), 'webui.py', '--port', str(RVC_PORT)],
                cwd=str(RVC_ROOT), env=env,
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        RVC_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        return {'ok': True, 'state': 'starting',
                'message': '正在启动 RVC WebUI（PID %d），首次约 1 分钟…' % proc.pid}
    except Exception as e:
        return {'ok': False, 'state': 'error',
                'message': '启动 RVC 失败：%s: %s' % (type(e).__name__, str(e)[:140])}


def _rvc_stop():
    pid = _rvc_pid()
    if not pid or not _rvc_pid_alive(pid):
        try:
            RVC_PIDFILE.unlink()
        except Exception:
            pass
        return {'ok': True, 'state': 'stopped', 'message': 'RVC 未在运行'}
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, text=True, errors='replace', timeout=15)
    except Exception:
        pass
    try:
        RVC_PIDFILE.unlink()
    except Exception:
        pass
    return {'ok': True, 'state': 'stopped', 'message': 'RVC 已停止（PID %d）' % pid}


def _rvc_status():
    pid = _rvc_pid()
    running = _rvc_pid_alive(pid)
    return {'ok': True, 'running': running, 'pid': pid if running else 0,
            'ready': _rvc_ready(), 'port': RVC_PORT, 'root': str(RVC_ROOT)}


# ---- 本地 GPT-SoVITS 图形面板（webui.py）----
def _gsv_webui_pid_alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                             capture_output=True, text=True, errors='replace', timeout=10)
        return str(pid) in out.stdout
    except Exception:
        return False


def _gsv_webui_ready():
    return _port_open('127.0.0.1', GSV_WEBUI_PORT)


def _gsv_webui_pid():
    try:
        return int(GSV_WEBUI_PIDFILE.read_text(encoding='utf-8').strip())
    except Exception:
        return 0


def _gsv_webui_start():
    """拉起 GPT-SoVITS webui.py（图形面板，独立于 api_v2.py）。"""
    if _gsv_webui_ready():
        return {'ok': True, 'state': 'ready', 'message': 'GPT-SoVITS 面板已在运行'}
    root = Path(str(_tts_cfg().get('gsv_root') or DEFAULT_GSV_ROOT))
    py = root / 'runtime' / 'python.exe'
    script = root / 'webui.py'
    if not py.exists():
        py = root / 'python.exe'
    if not script.exists() or not py.exists():
        return {'ok': False, 'state': 'error',
                'message': '找不到 GPT-SoVITS 的 webui.py 或 runtime/python.exe（请确认 gsv_root）'}
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        env = dict(os.environ)
        env['PATH'] = str(root / 'runtime') + os.pathsep + env.get('PATH', '')
        with GSV_WEBUI_LOG.open('ab') as out, GSV_WEBUI_ERRLOG.open('ab') as err:
            proc = subprocess.Popen(
                [str(py), 'webui.py', '--port', str(GSV_WEBUI_PORT), '--host', '127.0.0.1'],
                cwd=str(root), env=env,
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        GSV_WEBUI_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
        return {'ok': True, 'state': 'starting',
                'message': '正在启动 GPT-SoVITS 面板（PID %d），首次约 1 分钟…' % proc.pid}
    except Exception as e:
        return {'ok': False, 'state': 'error',
                'message': '启动 GPT-SoVITS 面板失败：%s: %s' % (type(e).__name__, str(e)[:140])}


def _gsv_webui_stop():
    pid = _gsv_webui_pid()
    if not pid or not _gsv_webui_pid_alive(pid):
        try:
            GSV_WEBUI_PIDFILE.unlink()
        except Exception:
            pass
        return {'ok': True, 'state': 'stopped', 'message': 'GPT-SoVITS 面板未在运行'}
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, text=True, errors='replace', timeout=15)
    except Exception:
        pass
    try:
        GSV_WEBUI_PIDFILE.unlink()
    except Exception:
        pass
    return {'ok': True, 'state': 'stopped', 'message': 'GPT-SoVITS 面板已停止（PID %d）' % pid}


def _gsv_webui_status():
    pid = _gsv_webui_pid()
    running = _gsv_webui_pid_alive(pid)
    return {'ok': True, 'running': running, 'pid': pid if running else 0,
            'ready': _gsv_webui_ready(), 'port': GSV_WEBUI_PORT, 'root': str(GSV_WEBUI_ROOT)}


def start_reminder():
    try:
        log=REMINDER/'startup-panel.err.log'
        # A PIPE can remain open in detached Windows children and make run()
        # wait for the long-lived reminder. A file avoids waiting for pipe EOF.
        with log.open('wb') as error_log:
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(REMINDER/'start.ps1')],cwd=str(REMINDER),creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=error_log,timeout=90)
        if result.returncode:
            START_STATUS.update(state='error',message='启动失败，已保存错误日志 startup-panel.err.log，请检查本地Python环境。')
            return
        with urllib.request.urlopen('http://127.0.0.1:31996/status',timeout=5) as response:status=json.load(response)
        message='本地提醒已启动。'
        if not status.get('qq_connected'):message+=' QQ尚未连接，请打开“个人QQ登录管理”确认登录。'
        START_STATUS.update(state='ready',message=message)
    except Exception as error:
        START_STATUS.update(state='error',message='启动未完成：'+type(error).__name__+'。请查看本地启动日志。')
    finally:
        START_LOCK.release()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BOTConsole/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):
        sys.stdout.write("[panel] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    # ---------- 路由 ----------
    def do_GET(self):
        if self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):
            return self._json(403,{'error':'invalid_host'})
        path = self.path.split("?")[0]
        assets={**{'/assets/workbench/'+name:mime for name,mime in [('style.css','text/css; charset=utf-8'),('app.js','text/javascript; charset=utf-8'),('unified.js','text/javascript; charset=utf-8'),('workflow-plus.js','text/javascript; charset=utf-8'),('landscape.jpeg','image/jpeg')]},'/assets/de-stijl.css':'text/css; charset=utf-8','/assets/landscape-reference-0.jpeg':'image/jpeg'}
        if path in assets:
            return self._send(200,(Path(HERE)/path.lstrip('/')).read_bytes(),assets[path])
        if path.startswith('/local/'):
            return self._local('GET',path)
        if path in ("/", "/index.html"):
            return self._serve_index("workbench.html")
        if path == "/tts.html":
            fp = os.path.join(HERE, "tts.html")
            if not os.path.exists(fp):
                return self._send(404, "404 Not Found", "text/plain; charset=utf-8")
            with open(fp, "rb") as f:
                data = f.read()
            data = data.replace(b'__LOCAL_TOKEN__', LOCAL_TOKEN.encode())
            return self._send(200, data, "text/html; charset=utf-8")
        if path == "/rvc.html":
            fp = os.path.join(HERE, "rvc.html")
            if not os.path.exists(fp):
                return self._send(404, "404 Not Found", "text/plain; charset=utf-8")
            with open(fp, "rb") as f:
                data = f.read()
            data = data.replace(b'__LOCAL_TOKEN__', LOCAL_TOKEN.encode())
            return self._send(200, data, "text/html; charset=utf-8")
        if path == "/legacy":
            return self._send(302,"",extra={"Location":"/#overview"})
        if path.startswith("/api/"):
            return self._proxy("GET")
        if path.startswith("/entry/"):
            return self._entry(path)
        if path == "/healthz":
            return self._json(200, {"ok": True, "upstream": self._upstream_alive()})
        return self._send(404, "404 Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path.startswith('/local/'):
            return self._local('POST',self.path)
        if self.path.startswith("/api/"):
            return self._proxy("POST")
        return self._send(405, "405", "text/plain; charset=utf-8")

    def do_HEAD(self):
        self._send(200, "", "text/html; charset=utf-8")

    # ---------- 首页 ----------
    def _serve_index(self,name="index.html"):
        fp = os.path.join(HERE, name)
        if not os.path.exists(fp):
            return self._send(500, "index.html 缺失", "text/plain; charset=utf-8")
        with open(fp, "rb") as f:
            data = f.read()
        data=data.replace(b'__LOCAL_REMINDER_TOKEN__', LOCAL_TOKEN.encode())
        return self._send(200, data, "text/html; charset=utf-8")

    def _local(self,method,path):
        allowed={'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}
        if self.headers.get('Host') not in {x.split('//')[1] for x in allowed}:
            return self._json(403,{'error':'invalid_host'})
        if method=='POST' and (self.headers.get('Origin') not in allowed or not secrets.compare_digest(self.headers.get('X-Local-Token',''),LOCAL_TOKEN)):
            return self._json(403,{'error':'invalid_origin_or_token'})
        try:
            if path=='/local/start' and method=='GET':return self._json(200,dict(START_STATUS))
            if path=='/local/start' and method=='POST':
                # Consume the body even for an argument-free action. Otherwise
                # '{}' prefixes the next request on the browser's reused socket.
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=1024:
                    self.close_connection=True
                    return self._json(400,{'error':'invalid_body_size'})
                body=self.rfile.read(length)
                if body and not isinstance(json.loads(body),dict):return self._json(400,{'error':'invalid_body'})
                if START_LOCK.acquire(blocking=False):
                    START_STATUS.update(state='starting',message='正在启动并检查本地服务…')
                    threading.Thread(target=start_reminder,daemon=True).start()
                return self._json(202,dict(START_STATUS))
            # ---- 本地 TTS（GPT-SoVITS）----
            if path.startswith('/local/tts/'):
                action=path[len('/local/tts/'):]
                if action=='status' and method=='GET':
                    return self._json(200,_tts_status())
                if action=='start' and method=='POST':
                    length=int(self.headers.get('Content-Length','0') or 0)
                    if length:self.rfile.read(length)
                    return self._json(200,_tts_start_client())
                if action=='stop' and method=='POST':
                    length=int(self.headers.get('Content-Length','0') or 0)
                    if length:self.rfile.read(length)
                    return self._json(200,_tts_stop_client())
                if action=='config' and method=='POST':
                    length=int(self.headers.get('Content-Length','0') or 0)
                    if not 0<length<=16384:
                        self.close_connection=True
                        return self._json(400,{'error':'invalid_body_size'})
                    try:
                        data=json.loads(self.rfile.read(length))
                    except Exception:
                        return self._json(400,{'error':'invalid_body'})
                    if not isinstance(data,dict):
                        return self._json(400,{'error':'invalid_body'})
                    return self._json(200,_tts_save_config(data))
                return self._json(404,{'error':'unknown_tts_action'})
            # ---- 本地 RVC（翻唱引擎）----
            if path.startswith('/local/rvc/'):
                action = path[len('/local/rvc/'):]
                if action == 'status' and method == 'GET':
                    return self._json(200, _rvc_status())
                if action == 'start' and method == 'POST':
                    length = int(self.headers.get('Content-Length', '0') or 0)
                    if length:
                        self.rfile.read(length)
                    return self._json(200, _rvc_start())
                if action == 'stop' and method == 'POST':
                    length = int(self.headers.get('Content-Length', '0') or 0)
                    if length:
                        self.rfile.read(length)
                    return self._json(200, _rvc_stop())
                return self._json(404, {'error': 'unknown_rvc_action'})
            # ---- 本地 GPT-SoVITS 图形面板（webui.py）----
            if path.startswith('/local/gsv/'):
                action = path[len('/local/gsv/'):]
                if action == 'status' and method == 'GET':
                    return self._json(200, _gsv_webui_status())
                if action == 'start' and method == 'POST':
                    length = int(self.headers.get('Content-Length', '0') or 0)
                    if length:
                        self.rfile.read(length)
                    return self._json(200, _gsv_webui_start())
                if action == 'stop' and method == 'POST':
                    length = int(self.headers.get('Content-Length', '0') or 0)
                    if length:
                        self.rfile.read(length)
                    return self._json(200, _gsv_webui_stop())
                return self._json(404, {'error': 'unknown_gsv_action'})
            if path not in ('/local/status','/local/history','/local/calibrate','/local/operations','/local/snowluma-log','/local/token-trend'):
                return self._json(404,{'error':'unknown_local_action'})
            if path=='/local/snowluma-log':
                return self._local_snowluma_log()
            if path=='/local/status' and method!='GET':return self._json(405,{'error':'method'})
            target='http://127.0.0.1:31996/'+path.rsplit('/',1)[1]
            if method=='GET' and path=='/local/operations' and '?' in self.path:
                from urllib.parse import parse_qs
                offset=parse_qs(self.path.split('?',1)[1]).get('files_offset',['0'])[0]
                if not offset.isdigit() or int(offset)>10000000:return self._json(400,{'error':'invalid_offset'})
                from urllib.parse import urlencode
                query=parse_qs(self.path.split('?',1)[1])
                filters={k:query.get(k,[''])[0][:200] for k in ('q','platform','start','end')}
                target+='?'+urlencode(dict(files_offset=offset,**filters))
            if method=='GET' and path=='/local/history' and '?' in self.path:
                from urllib.parse import parse_qs, urlencode
                report=parse_qs(self.path.split('?',1)[1]).get('report',[''])[0]
                if not report or len(report)>40 or any(c not in '0123456789.' for c in report):return self._json(400,{'error':'invalid_report'})
                target+='?'+urlencode({'report':report})
            headers={};body=None
            if path.endswith(('history','calibrate','operations')):
                # 密钥只在本机后端解密，不返回给网页或云端机器人。
                if str(REMINDER) not in sys.path:sys.path.insert(0,str(REMINDER))
                from vault import unseal
                secret=json.loads(unseal((REMINDER/'secrets.dpapi').read_text()))
                headers['Authorization']='Bearer '+secret['callback_token']
                if method=='POST':
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=16384:return self._json(400,{'error':'invalid_body_size'})
                    value=json.loads(self.rfile.read(length))
                    if path.endswith('history'):
                        if value.get('action')=='cancel':
                            value={'action':'cancel'}
                        else:
                            if type(value.get('days')) is not int or value['days'] not in (1,3,7):return self._json(400,{'error':'invalid_days'})
                            source=value.get('source','all')
                            if source not in ('all','qq','wechat'):return self._json(400,{'error':'invalid_source'})
                            selected=value.get('groups')
                            value={'days':value['days'],'source':source}
                            if selected is not None:
                                if not isinstance(selected,list) or not 1<=len(selected)<=10 or any(not isinstance(k,str) or len(k)>150 for k in selected):return self._json(400,{'error':'invalid_groups'})
                                value['groups']=selected
                    elif path.endswith('operations'):
                        if value.get('action') in ('backup','cleanup','full_start','full_resume','full_pause','transfer_files','notification_targets','scan_preview','full_skip','full_stop','full_retry','source_focus','matter_feedback','deadline_reminder','file_check','file_resolve','reference_metadata','batch-tuning','retry_all_failed'):value.setdefault('id','workspace')
                        if value.get('action') not in ('done','retry_analysis','retry_delivery','dismiss_delivery','focus_group','preferences','group_settings','star','snooze','schedule_reminder','cancel_reminder','retry_delivery_failed','backup','cleanup','link_matter','unlink_matter','full_start','full_resume','full_pause','transfer_files','notification_targets','scan_preview','full_skip','full_stop','full_retry','source_focus','matter_feedback','deadline_reminder','file_check','file_resolve','reference_metadata','batch-tuning','retry_all_failed') or not isinstance(value.get('id'),str) or len(value['id'])>150:return self._json(400,{'error':'invalid_operation'})
                        value={k:v for k,v in value.items() if k in ('action','id','done','confirmed','focused','preferences','level','delivery','enabled','due','hide','child','files','file_group','recipient','expected_group','scope','focus','choice','duplicate_of','deadline','lead','category','tags','reset','batch_seconds','quiet_seconds')}
                    else:
                        if type(value.get('remaining')) is not int or not 0<=value['remaining']<=15000000:return self._json(400,{'error':'invalid_remaining'})
                        value={'remaining':value['remaining']}
                    body=json.dumps(value).encode();headers['Content-Type']='application/json'
            req=urllib.request.Request(target,data=body,headers=headers,method=method)
            with urllib.request.urlopen(req,timeout=5) as response:
                return self._send(response.status,response.read(),'application/json; charset=utf-8')
        except urllib.error.HTTPError as error:
            return self._send(error.code,error.read(),'application/json; charset=utf-8')
        except Exception:
            return self._json(503,{'error':'local_reminder_unavailable','message':'本地提醒服务未就绪，请启动后刷新。'})

    # ---------- 本地 SnowLuma 日志 ----------
    def _local_snowluma_log(self):
        import datetime, collections
        try:
            base = Path(HERE).parent
            logdir = base / 'snowluma相关' / 'runtime-v1.14.22' / 'logs'
            if not logdir.is_dir():
                return self._json(200, {'ok': False, 'error': 'no_logdir',
                                        'message': '未找到本地 SnowLuma 日志目录。'})
            today = datetime.date.today().strftime('%Y-%m-%d')
            cand = logdir / ('snowluma-%s.log' % today)
            if not cand.is_file():
                files = sorted(logdir.glob('snowluma-*.log'),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                cand = files[0] if files else None
            if not cand or not cand.is_file():
                return self._json(200, {'ok': False, 'error': 'no_log',
                                        'message': '未找到本地 SnowLuma 日志文件。'})
            if not str(cand.resolve()).startswith(str(logdir.resolve())):
                return self._json(403, {'error': 'forbidden'})
            with open(cand, 'r', encoding='utf-8', errors='replace') as f:
                lines = collections.deque(f, maxlen=300)
            st = cand.stat()
            return self._json(200, {
                'ok': True,
                'file': str(cand.relative_to(base)),
                'size': st.st_size,
                'mtime': datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'lines': list(lines),
            })
        except Exception as e:
            return self._json(200, {'ok': False, 'error': 'read_error',
                                    'message': str(e)[:200]})

    # ---------- 入口探测 ----------
    def _entry(self, path):
        name = path.split("/")[-1]
        port = ENTRY_PORTS.get(name)
        if not port:
            return self._json(404, {"ok": False, "error": "unknown_entry"})
        alive = self._tcp_alive(port)
        self._json(200, {"ok": True, "entry": name, "port": port, "alive": alive,
                         "url": "http://127.0.0.1:%d" % port})

    @staticmethod
    def _tcp_alive(port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.2)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False
        finally:
            try:
                s.close()
            except Exception:
                pass

    @staticmethod
    def _upstream_alive():
        try:
            with urllib.request.urlopen(UPSTREAM + "/api/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ---------- API 代理 ----------
    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        # 透传关键请求头
        fwd = {}
        for h in ("Content-Type", "X-CSRF-Token", "Cookie", "Accept"):
            v = self.headers.get(h)
            if v:
                fwd[h] = v

        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=method,
                                     headers=fwd)
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
                headers = []
                for h in ("Content-Type", "Set-Cookie"):
                    v = resp.headers.get(h)
                    if v:
                        headers.append((h, v))
                return self._send(resp.status, data,
                                  resp.headers.get("Content-Type", "application/json"),
                                  dict(headers))
        except urllib.error.HTTPError as e:
            data = e.read()
            headers = []
            for h in ("Content-Type", "Set-Cookie"):
                v = e.headers.get(h)
                if v:
                    headers.append((h, v))
            return self._send(e.code, data,
                              e.headers.get("Content-Type", "application/json"),
                              dict(headers))
        except Exception as e:
            return self._json(502, {"ok": False,
                                    "error": "upstream_unreachable",
                                    "message": "无法连接服务器面板 API（SSH 隧道是否已断开？）",
                                    "detail": str(e)[:200]})


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = PANEL_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    srv = ThreadedServer(("127.0.0.1", port), Handler)
    print("=" * 62)
    print("  BOT CONSOLE · 本地管理面板已启动")
    print("=" * 62)
    print("  面板地址 : http://127.0.0.1:%d" % port)
    print("  上游 API : %s" % UPSTREAM)
    print("  入口     : astrbot=6185  snowluma=5099  novnc=6081")
    print("  停止     : 在本窗口按 Ctrl+C")
    print("=" * 62)
    sys.stdout.flush()

    # 隧道看门狗：保证本机 ↔ 服务器的 SSH 隧道始终在线（断了自动重连）。
    threading.Thread(target=_tunnel_watchdog, daemon=True).start()
    # 启动即尝试建立一次隧道（若 bat 已建则自动跳过）。
    threading.Thread(target=_tunnel_start, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[panel] 正在关闭…")
    finally:
        srv.shutdown()
        srv.server_close()
        print("[panel] 已退出")


if __name__ == "__main__":
    main()
