"""管理面板 API 小工具 + 客户端重启。"""
import json
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def token():
    # 面板已改成 SPA：token 在首页的 <meta name="local-token">，
    # /tts.html 现在只是跳 /#tts 的空壳页。
    for path in ("/", "/tts.html"):
        try:
            html = urllib.request.urlopen(BASE + path, timeout=10).read().decode("utf-8", "ignore")
        except Exception:
            continue
        for pat in (
            r'name=["\']local-token["\'][^>]*content=["\']([^"\']+)',
            r'content=["\']([^"\']+)["\'][^>]*name=["\']local-token',
            r'name=["\']local-token["\']\s+content=["\']([^"\']+)',
        ):
            m = re.search(pat, html)
            if m:
                return m.group(1)
        m = re.search(r'["\']([A-Za-z0-9_\-]{40,50})["\']', html)
        if m:
            return m.group(1)
    raise RuntimeError("取不到面板 token")


def call(path, body=None, timeout=60):
    t = token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + "/" + path, data=data,
        headers={"Content-Type": "application/json", "X-Local-Token": t, "Origin": BASE},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def restart_client(wait=200):
    print("停止:", (call("local/tts/stop", {}) or {}).get("message", "")[:50])
    time.sleep(2)
    print("启动:", (call("local/tts/start", {}) or {}).get("message", "")[:50])
    t0 = time.time()
    last = None
    s = {}
    while time.time() - t0 < wait:
        time.sleep(5)
        s = call("local/tts/status")
        L = s.get("launch") or {}
        cur = (bool(s.get("running")), bool(s.get("server_online")), L.get("state"))
        if cur != last:
            print("  [%3ds] running=%s online=%s | %s"
                  % (time.time() - t0, cur[0], cur[1], L.get("message")))
            last = cur
        if cur[1]:
            break
    return s


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "restart":
        s = restart_client()
        print("最终: running=%s api_ready=%s online=%s"
              % (s.get("running"), s.get("api_ready"), s.get("server_online")))
    else:
        s = call("local/tts/status")
        print("running=%s api_ready=%s online=%s"
              % (s.get("running"), s.get("api_ready"), s.get("server_online")))
