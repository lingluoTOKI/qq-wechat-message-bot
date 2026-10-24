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
import secrets
import subprocess
from pathlib import Path
import urllib.error
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
            if path not in ('/local/status','/local/history','/local/calibrate','/local/operations','/local/snowluma-log'):
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
                        if value.get('action') in ('backup','cleanup','full_start','full_resume','full_pause','transfer_files','notification_targets','scan_preview','full_skip','full_stop','full_retry','source_focus','matter_feedback','deadline_reminder','file_check','file_resolve','reference_metadata'):value.setdefault('id','workspace')
                        if value.get('action') not in ('done','retry_analysis','retry_delivery','dismiss_delivery','focus_group','preferences','group_settings','star','snooze','schedule_reminder','cancel_reminder','retry_delivery_failed','backup','cleanup','link_matter','unlink_matter','full_start','full_resume','full_pause','transfer_files','notification_targets','scan_preview','full_skip','full_stop','full_retry','source_focus','matter_feedback','deadline_reminder','file_check','file_resolve','reference_metadata') or not isinstance(value.get('id'),str) or len(value['id'])>150:return self._json(400,{'error':'invalid_operation'})
                        value={k:v for k,v in value.items() if k in ('action','id','done','confirmed','focused','preferences','level','delivery','enabled','due','hide','child','files','file_group','recipient','expected_group','scope','focus','choice','duplicate_of','deadline','lead','category','tags','reset')}
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
