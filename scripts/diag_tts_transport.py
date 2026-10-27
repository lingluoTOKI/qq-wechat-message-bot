#!/usr/bin/env python3
"""诊断：绕过 fake-ip DNS，直接用服务器真实 IP + SNI 访问 HTTPS。

背景：本机 DNS 把 game.lingluotoki.dpdns.org 解析成 198.18.0.54（fake-ip，
由 Clash/v2ray 之类工具伪造），但该域名的规则是直连，直连 fake-ip 会 TLS 断连。
若「真实 IP + SNI」可用，就让客户端在域名失败时自动改走这条路。
"""
import json
import socket
import ssl
import sys

HOST = "game.lingluotoki.dpdns.org"
REAL_IP = "101.132.104.41"
CFG = r"F:\github\BOT用\管理面板\本地TTS\config.json"


def https_get(ip, host, path, token, verify=True):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((ip, 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            req = (
                "GET %s HTTP/1.1\r\nHost: %s\r\nX-TTSToken: %s\r\n"
                "Connection: close\r\n\r\n" % (path, host, token)
            )
            ss.sendall(req.encode())
            buf = b""
            while True:
                chunk = ss.recv(4096)
                if not chunk:
                    break
                buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n")[0].decode("latin1")
    return status, body.decode("utf-8", "replace")


def main():
    tok = json.load(open(CFG, encoding="utf-8-sig"))["server_token"]

    print("=== 1. 域名解析（本机 DNS，可能是 fake-ip）===")
    try:
        print("  %s -> %s" % (HOST, socket.gethostbyname(HOST)))
    except Exception as e:
        print("  解析失败:", e)

    print("\n=== 2. 真实 IP + SNI（校验证书）===")
    try:
        st, body = https_get(REAL_IP, HOST, "/tts/health", tok, verify=True)
        print("  ", st)
        print("  ", body[:120])
        ok = "200" in st
    except Exception as e:
        print("   失败:", type(e).__name__, str(e)[:160])
        ok = False

    if not ok:
        print("\n=== 3. 真实 IP + SNI（不校验证书）===")
        try:
            st, body = https_get(REAL_IP, HOST, "/tts/health", tok, verify=False)
            print("  ", st)
            print("  ", body[:120])
            ok = "200" in st
        except Exception as e:
            print("   失败:", type(e).__name__, str(e)[:160])

    print("\n结论:", "真实 IP 可用 → 客户端可自动回退到这条路" if ok else "真实 IP 也不通，需要换传输方式")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
