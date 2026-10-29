#!/usr/bin/env python3
"""让面板的在线检测也优先走本地隧道（与客户端保持一致）。

否则会出现「客户端明明连上了，页面却显示未连接」——
因为面板查的是公网 HTTPS，而这台机器直连该域名会被 TLS 断连。
"""
import py_compile
import re
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\panel_server.py")
src = P.read_text(encoding="utf-8")

old = """    token = str(cfg.get('server_token') or '')
    url = str(cfg.get('server_url') or TTS_SERVER).rstrip('/') + '/health'
    last_err = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={'X-TTSToken': token} if token else {})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode('utf-8'))
            return bool(data.get('online')), data
        except Exception as e:
            last_err = e
    return False, {'error': type(last_err).__name__, 'detail': str(last_err)[:120]}"""

new = """    token = str(cfg.get('server_token') or '')
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
    return False, {'error': type(last_err).__name__, 'detail': str(last_err)[:120]}"""

assert old in src, "未匹配到 _tts_server_online 主体"
src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("panel_server.py 在线检测已改为隧道优先，语法通过")
print("  含 server_url_local:", "server_url_local" in src)
print("  via 标记:", src.count("'via'"))
