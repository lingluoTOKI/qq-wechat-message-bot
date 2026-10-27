#!/usr/bin/env python3
"""本地 TTS 代理服务（服务器侧）

作用：让用户本机运行的 GPT-SoVITS 能被服务器上的 AstrBot 调用，
      同时在电脑关机/离线时自动让调用方回落（超时即回落）。

协议（纯 HTTP + 标准库，无第三方依赖）
------------------------------------
POST /poll      客户端领取任务（长轮询，最多等 20 秒）
                → {"task_id": "...", "text": "..."} 或 {"task_id": null}
POST /result    客户端回传音频
                → {"task_id": "...", "audio_b64": "..."} / {"task_id":"...","error":"..."}
POST /synth     服务器内部调用：入队并等待结果（超时返回 408）
                → {"ok": true, "audio_b64": "..."} 或 408
GET  /health    健康检查
                → {"ok": true, "online": true/false, "pending": n}

鉴权：所有请求需带 header `X-TTSToken: <token>`
"""
import base64
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("LTT_HOST", "0.0.0.0")
PORT = int(os.environ.get("LTT_PORT", "32000"))
TOKEN = os.environ.get("LTT_TOKEN", "")
POLL_WAIT = 20.0        # 长轮询最长等待秒数
SYNTH_TIMEOUT = float(os.environ.get("LTT_SYNTH_TIMEOUT", "8"))

_pending = queue.Queue()      # 待领取的任务
_results = {}                 # task_id -> (event, result)
_lock = threading.Lock()
_last_poll = 0.0              # 客户端最近一次 poll 的时间


def _now():
    return time.time()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # 静默，避免刷屏

    # ---------- 工具 ----------
    def _auth(self):
        if not TOKEN:
            return True
        return self.headers.get("X-TTSToken", "") == TOKEN

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        if not self._auth():
            return self._send(403, {"ok": False, "error": "forbidden"})
        if self.path.startswith("/health"):
            with _lock:
                online = (_now() - _last_poll) < 30
            return self._send(200, {"ok": True, "online": online,
                                     "pending": _pending.qsize(),
                                     "time": _now()})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        global _last_poll
        if not self._auth():
            return self._send(403, {"ok": False, "error": "forbidden"})
        body = self._read_json()

        if self.path.startswith("/poll"):
            with _lock:
                _last_poll = _now()
            try:
                task = _pending.get(timeout=POLL_WAIT)
            except queue.Empty:
                return self._send(200, {"task_id": None})
            return self._send(200, task)

        if self.path.startswith("/result"):
            tid = body.get("task_id")
            with _lock:
                slot = _results.get(tid)
                if slot is not None:
                    slot[0].set()
                    slot[1] = body
            return self._send(200, {"ok": True})

        if self.path.startswith("/synth"):
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"ok": False, "error": "empty text"})
            tid = "t%d_%d" % (int(_now() * 1000), threading.get_ident() % 100000)
            ev = threading.Event()
            with _lock:
                _results[tid] = [ev, None]
            _pending.put({"task_id": tid, "text": text})
            got = ev.wait(SYNTH_TIMEOUT)
            with _lock:
                slot = _results.pop(tid, None)
            if not got or not slot or not slot[1]:
                # 本机没接单（关机/离线）→ 明确告诉调用方回落
                with _lock:
                    try:
                        _pending.get_nowait()   # 别把没人接的任务留在队列里
                    except queue.Empty:
                        pass
                return self._send(408, {"ok": False, "error": "local tts unavailable"})
            res = slot[1]
            if res.get("error"):
                return self._send(500, {"ok": False, "error": res.get("error")})
            return self._send(200, {"ok": True, "audio_b64": res.get("audio_b64", "")})

        return self._send(404, {"ok": False, "error": "not found"})


def main():
    if not TOKEN:
        print("[local-tts] 警告：LTT_TOKEN 未设置，接口无鉴权！", flush=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("[local-tts] 监听 %s:%d（synth 超时 %.1fs）" % (HOST, PORT, SYNTH_TIMEOUT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
