#!/usr/bin/env python3
"""修三处，解决「本地合成总是 408 → 回落 Edge（音色不对）」。

在**服务器**上跑：改 local_tts_server.py 的动态超时。
在**本机**上跑：改 local_tts_client.py 的预热与重连间隔。
两个文件哪个存在就改哪个，都缺席才报错。

背景：
- 原来固定 8 秒超时；GPT-SoVITS 空闲后第一句要 8~13 秒（GPU 冷），必超 → 回落 Edge → 音色不对。
- 改动态：客户端在线 25s，离线 1s（关机时回落更快）。
- 客户端启动后预热一句，避免第一条真请求吃冷启动。
- 重连间隔 5s → 2s，缩小「任务来了但客户端在睡觉」的窗口。
"""
import os
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
SRV = Path("/opt/bot-stack/bin/local_tts_server.py")
CLI_CANDIDATES = [
    Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py"),
    Path("/opt/bot-stack/scripts/local_tts_client.py"),
]

done = []


def backup_and_compile(p):
    shutil.copy(p, "%s.bak-%s" % (p, STAMP))
    py_compile.compile(str(p), doraise=True)


# ---------------- 服务器：动态超时 ----------------
if SRV.exists():
    s = SRV.read_text(encoding="utf-8")
    if "SYNTH_TIMEOUT_ONLINE" in s:
        print("服务器：已是动态超时，跳过")
    else:
        backup_and_compile_backup = shutil.copy(SRV, "%s.bak-timeout-%s" % (SRV, STAMP))
        old = 'SYNTH_TIMEOUT = float(os.environ.get("LTT_SYNTH_TIMEOUT", "8"))'
        new = ('SYNTH_TIMEOUT = float(os.environ.get("LTT_SYNTH_TIMEOUT", "8"))\n'
               '# 动态超时：客户端在线给足时间（GPU 冷启动慢），离线立刻失败以便快速回落\n'
               'SYNTH_TIMEOUT_ONLINE = float(os.environ.get("LTT_SYNTH_TIMEOUT_ONLINE", "25"))\n'
               'SYNTH_TIMEOUT_OFFLINE = float(os.environ.get("LTT_SYNTH_TIMEOUT_OFFLINE", "1"))')
        if old not in s:
            print("!! 服务器：SYNTH_TIMEOUT 定义未匹配")
            sys.exit(1)
        s = s.replace(old, new, 1)

        old2 = """            _pending.put({"task_id": tid, "text": text})
            got = ev.wait(SYNTH_TIMEOUT)"""
        new2 = """            _pending.put({"task_id": tid, "text": text})
            # 客户端 30 秒内 poll 过 = 在线 → 给 25s；否则多半关机 → 1s 快速回落
            with _lock:
                _online_now = (_now() - _last_poll) < 30
            _wait = SYNTH_TIMEOUT_ONLINE if _online_now else SYNTH_TIMEOUT_OFFLINE
            got = ev.wait(_wait)"""
        if old2 not in s:
            print("!! 服务器：synth 等待处未匹配")
            sys.exit(1)
        s = s.replace(old2, new2, 1)
        SRV.write_text(s, encoding="utf-8")
        py_compile.compile(str(SRV), doraise=True)
        print("1. 服务器：动态超时（在线 25s / 离线 1s）已生效")
        done.append("server")
else:
    print("（跳过服务器部分）")

# ---------------- 本机：预热 + 重连 ----------------
cli = next((p for p in CLI_CANDIDATES if p.exists()), None)
if cli:
    c = cli.read_text(encoding="utf-8")
    changed = False

    old = """            print("[断线] %s: %s，5 秒后重连" % (type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(5)"""
    new = """            print("[断线] %s: %s，2 秒后重连" % (type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(2)"""
    if old in c:
        c = c.replace(old, new, 1)
        changed = True
        print("3. 客户端：重连间隔 5s → 2s")
    else:
        print("  （客户端重连片段已是新值或未匹配，跳过）")

    anchor = '    print("→ 已连上服务器，等待任务…（Ctrl+C 退出）")'
    if "预热完成" not in c and anchor in c:
        warm = '''    # 预热：先合成一句极短文本，把 GPU kernel 烤热。
    # 不做的话，空闲后第一句真请求要 8~13 秒，会超服务器超时被判 408 → 回落 Edge。
    try:
        _tw = time.time()
        _warm = synth("预热")
        if _warm:
            print("  预热完成（%.1f 秒），首句不再冷启动" % (time.time() - _tw))
        else:
            print("  预热未返回音频（不影响后续）")
    except Exception as _we:
        print("  预热失败（不影响后续）: %s" % type(_we).__name__)

'''
        c = c.replace(anchor, warm + anchor, 1)
        changed = True
        print("2. 客户端：已插入预热")
    else:
        print("  （客户端预热已存在或锚点未匹配）")

    if changed:
        shutil.copy(cli, "%s.bak-warmup-%s" % (cli, STAMP))
        cli.write_text(c, encoding="utf-8")
        py_compile.compile(str(cli), doraise=True)
        print("   客户端语法通过：", cli)
        done.append("client")
else:
    print("（跳过客户端部分）")

print()
print("完成:", done or "无改动")
