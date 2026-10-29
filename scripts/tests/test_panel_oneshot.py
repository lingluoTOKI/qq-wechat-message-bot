#!/usr/bin/env python3
"""测试面板「一键启动」：拉 GPT-SoVITS -> 等就绪 -> 起客户端 -> 连上服务器。

针对临时实例（默认 8766）跑，不动用户正在用的 8765 面板。
"""
import json
import re
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
BASE = "http://127.0.0.1:%d" % PORT


def get_token():
    with urllib.request.urlopen(BASE + "/tts.html", timeout=10) as r:
        html = r.read().decode("utf-8", "ignore")
    m = re.search(r'name=["\']local-token["\']\s+content=["\']([^"\']+)', html)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\']\s+name=["\']local-token', html)
    if not m:
        raise SystemExit("!! 没能从页面解析出 local-token")
    return m.group(1)


def call(path, token, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "X-Local-Token": token,
               # 面板对 POST 做同源校验，缺 Origin 会 403
               "Origin": BASE}
    req = urllib.request.Request(
        BASE + "/" + path, data=data, headers=headers,
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    tok = get_token()
    print("local-token 长度:", len(tok))

    print("\n=== 1. 启动前状态 ===")
    s = call("local/tts/status", tok)
    print("  running=%s  api_ready=%s  server_online=%s  launch=%s"
          % (s.get("running"), s.get("api_ready"), s.get("server_online"),
             (s.get("launch") or {}).get("state")))

    print("\n=== 2. 点『一键启动』 ===")
    r = call("local/tts/start", tok, {})
    print("  ", r.get("state"), "|", r.get("message"))

    print("\n=== 3. 轮询进度（最多 240 秒）===")
    t0 = time.time()
    last = None
    while time.time() - t0 < 240:
        time.sleep(3)
        s = call("local/tts/status", tok)
        L = s.get("launch") or {}
        cur = (L.get("state"), L.get("message"))
        if cur != last:
            print("  [%3ds] %-16s %s" % (time.time() - t0, L.get("state"), L.get("message")))
            last = cur
        if L.get("state") in ("ready", "error"):
            break

    print("\n=== 4. 最终状态 ===")
    s = call("local/tts/status", tok)
    print("  api_ready     :", s.get("api_ready"), "(pid %s)" % s.get("api_pid"))
    print("  client running:", s.get("running"), "(pid %s)" % s.get("pid"))
    print("  server_online :", s.get("server_online"))
    print("  server_raw    :", json.dumps(s.get("server_raw"), ensure_ascii=False)[:160])
    if s.get("error_log"):
        print("  error_log     :", s["error_log"][-300:])
    ok = s.get("api_ready") and s.get("running") and s.get("server_online")
    print("\n结果:", "全部就绪 ✅" if ok else "未完全就绪 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
