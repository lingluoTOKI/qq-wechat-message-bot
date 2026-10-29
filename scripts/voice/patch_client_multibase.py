#!/usr/bin/env python3
"""让客户端支持「多路服务器地址」：优先本地 SSH 隧道，失败回落公网 HTTPS。

背景（实测）：这台机器的 DNS 被代理工具改成 fake-ip（game... → 198.18.0.54），
而该域名在代理规则里是直连 → 直连 fake-ip 直接 TLS 断连（SSLEOFError）。
面板自己启动的客户端没有代理环境变量，所以一直在 [断线] 循环。

解法：面板启动时已经建了 SSH 隧道，给它加 -L 32000:127.0.0.1:32000，
客户端改走 http://127.0.0.1:32000 —— 纯 HTTP 走 SSH，绕开 DNS/TLS/代理全部问题。
公网 HTTPS 保留为第二选择。
"""
import py_compile
import re
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
src = P.read_text(encoding="utf-8")

# 1) 新增本地隧道地址常量
old = 'SERVER_URL = "https://game.lingluotoki.dpdns.org/tts"\n'
new = ('SERVER_URL = "https://game.lingluotoki.dpdns.org/tts"\n'
       '# 本地 SSH 隧道地址（面板的隧道会转发 32000）。优先用它：纯 HTTP 走 SSH，\n'
       '# 不受本机 fake-ip DNS / TLS 拦截影响。留空则只用 SERVER_URL。\n'
       'SERVER_URL_LOCAL = "http://127.0.0.1:32000"\n'
       'BASE_URL = None          # 解析后的实际地址（运行时决定）\n')
assert old in src
src = src.replace(old, new, 1)

# 2) _load_config 读 server_url_local
old = "    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH\n"
new = "    global SERVER_URL, SERVER_TOKEN, GPT_API, GPT_MODEL_PATH, GPT_CKPT_PATH\n    global SERVER_URL_LOCAL\n"
assert old in src
src = src.replace(old, new, 1)

old = '    SERVER_URL = c.get("server_url") or SERVER_URL\n'
new = ('    SERVER_URL = c.get("server_url") or SERVER_URL\n'
       '    SERVER_URL_LOCAL = c.get("server_url_local") or SERVER_URL_LOCAL\n')
assert old in src
src = src.replace(old, new, 1)

# 3) 插入地址解析函数（放在 poll 之前）
anchor = "def poll():"
assert anchor in src
helper = '''def _health_of(base, timeout=6):
    """探一下某个 base 是否可用（/health 返回 200）。"""
    url = base.rstrip("/") + "/health"
    h = {}
    if SERVER_TOKEN:
        h["X-TTSToken"] = SERVER_TOKEN
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _base(force=False):
    """决定用哪个服务器地址：本地隧道优先，其次公网。

    结果缓存；force=True 时重新探测（隧道断开/恢复时会用到）。
    """
    global BASE_URL
    if BASE_URL and not force:
        return BASE_URL
    cands = []
    if SERVER_URL_LOCAL:
        cands.append(("本地隧道", SERVER_URL_LOCAL))
    cands.append(("公网", SERVER_URL))
    for label, base in cands:
        if _health_of(base):
            if BASE_URL != base:
                print("  服务器通道: %s（%s）" % (label, base), flush=True)
            BASE_URL = base
            return base
    BASE_URL = None
    return None


'''
src = src.replace(anchor, helper + anchor, 1)

# 4) poll / post_result 用 _base()
src = src.replace(
    '    url = SERVER_URL.rstrip("/") + "/poll"',
    '    base = _base()\n    if not base:\n        raise RuntimeError("没有可用的服务器通道")\n    url = base.rstrip("/") + "/poll"', 1)
src = src.replace(
    '    url = SERVER_URL.rstrip("/") + "/result"',
    '    base = _base()\n    if not base:\n        raise RuntimeError("没有可用的服务器通道")\n    url = base.rstrip("/") + "/result"', 1)

# 5) 断线时重新探测通道
old = """        except Exception as e:
            print("[断线] %s: %s，5 秒后重连" % (type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(5)
            continue"""
new = """        except Exception as e:
            print("[断线] %s: %s，5 秒后重连" % (type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(5)
            _base(force=True)     # 隧道可能断了/恢复了，重新选路上车
            continue"""
assert old in src
src = src.replace(old, new, 1)

# 6) 启动时先解析通道，并打印
old = '''    print("服务器  :", SERVER_URL)'''
new = '''    print("服务器  :", SERVER_URL)
    if SERVER_URL_LOCAL:
        print("本地隧道:", SERVER_URL_LOCAL, "（优先）")'''
assert old in src
src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("local_tts_client.py 已支持多路地址，语法通过")
for kw in ("SERVER_URL_LOCAL", "_base(", "_health_of", "BASE_URL"):
    print("  %-18s %d 次" % (kw, len(re.findall(re.escape(kw), src))))
