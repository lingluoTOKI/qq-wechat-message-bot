"""在 nginx 的 443 站点里插入本地 TTS 代理反代（/tts/*）
这样用户电脑只需访问 https://game.lingluotoki.dpdns.org/tts 即可，
不用额外开放阿里云安全组端口。鉴权由 X-TTSToken 头完成（应用层）。
"""
import re
import shutil
from datetime import datetime

CONF = "/etc/nginx/sites-enabled/game.conf"
shutil.copy(CONF, CONF + ".bak-ltt-" + datetime.now().strftime("%Y%m%d-%H%M"))

s = open(CONF, encoding="utf-8").read()
if "/tts/" in s:
    print("已存在 /tts/ 反代，跳过")
    raise SystemExit(0)

BLOCK = """
    # 本地 TTS 代理（GPT-SoVITS @ 用户电脑）：/tts/* -> 127.0.0.1:32000/*
    # 应用层用 X-TTSToken 鉴权；长轮询需要较长读超时
    location /tts/ {
        proxy_pass http://127.0.0.1:32000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
"""

# 插到 443 server 块里（server_name 之后的第一处 location 之前）
anchor = "    # 备用服（纯原版）反代：/backup/* -> http://116.62.39.28:3000/*"
if anchor in s:
    s = s.replace(anchor, BLOCK.strip("\n") + "\n\n" + anchor, 1)
else:
    m = re.search(r"(server \{\s*\n\s*listen 443 ssl;.*?\n)", s, re.S)
    if not m:
        print("未找到 443 server 块，未修改")
        raise SystemExit(1)
    s = s[:m.end(1)] + BLOCK + s[m.end(1):]

open(CONF, "w", encoding="utf-8").write(s)
print("已插入 /tts/ 反代")
