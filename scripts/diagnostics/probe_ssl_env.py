#!/usr/bin/env python3
"""隔离实验：用面板相同的创建标志（DETACHED_PROCESS|CREATE_NO_WINDOW）
启动子进程去请求服务器，判断 SSL 失败是「启动标志/环境」引起的，还是网络问题。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(r"F:\github\BOT用\管理面板\本地TTS", "config.json")
OUT = os.path.join(HERE, "_probe_out.txt")

CHILD = r'''
import json, sys, ssl, urllib.request, os
cfg = json.load(open(r"%s", encoding="utf-8-sig"))
tok = cfg["server_token"]
url = cfg["server_url"].rstrip("/") + "/health"
lines = []
lines.append("HTTPS_PROXY=%%s HTTP_PROXY=%%s https_proxy=%%s" %% (
    os.environ.get("HTTPS_PROXY"), os.environ.get("HTTP_PROXY"), os.environ.get("https_proxy")))
lines.append("openssl: " + ssl.OPENSSL_VERSION)
lines.append("python: " + sys.version.split()[0])
try:
    req = urllib.request.Request(url, headers={"X-TTSToken": tok})
    with urllib.request.urlopen(req, timeout=20) as r:
        lines.append("OK %%s %%s" %% (r.status, r.read().decode()[:80]))
except Exception as e:
    lines.append("FAIL %%s: %%s" %% (type(e).__name__, str(e)[:160]))
open(r"%s", "w", encoding="utf-8").write("\n".join(lines))
''' % (CFG, OUT)


def run(label, flags):
    if os.path.exists(OUT):
        os.remove(OUT)
    p = subprocess.Popen([sys.executable, "-c", CHILD],
                         creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if os.path.exists(OUT):
            break
    print("=== %s ===" % label)
    if os.path.exists(OUT):
        print(open(OUT, encoding="utf-8").read())
    else:
        print("  (20 秒内无输出，pid=%d)" % p.pid)
    print()


def main():
    print("父进程 python:", sys.version.split()[0])
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        if os.environ.get(k):
            print("  父进程环境 %s=%s" % (k, os.environ[k]))
    print()
    run("A. 普通启动（同 bash 运行）", 0)
    run("B. DETACHED_PROCESS | CREATE_NO_WINDOW（同面板）", 0x08000000 | 0x00000008)


if __name__ == "__main__":
    main()
