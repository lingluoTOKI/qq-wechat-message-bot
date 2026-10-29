"""修：local-tts 服务重启后的「首次请求误判离线」。

现象：`systemctl restart local-tts` 之后第一条 /synth 必然 408（1 秒）。
原因：`_last_poll` 是进程内全局变量，重启归零 → 判为离线 → 用 1s 超时；
      而客户端的长轮询要等下个周期（最长 20s）才重新上报，来不及。

修法：给启动后 60 秒一个「宽限期」，这期间最少等 6 秒。
      超过 60 秒后仍按原逻辑（客户端真离线就是 1s 快速回落）。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/bin/local_tts_server.py")
bak = "%s.bak-grace-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

old = '''_last_poll = 0.0              # 客户端最近一次 poll 的时间
'''
new = '''_last_poll = 0.0              # 客户端最近一次 poll 的时间
_started_at = time.time()     # 进程启动时刻（用于启动宽限期）
GRACE_SECONDS = float(os.environ.get("LTT_GRACE_SECONDS", "60"))
GRACE_WAIT = float(os.environ.get("LTT_GRACE_WAIT", "6"))
'''
assert old in s, "_last_poll 定义未匹配"
s = s.replace(old, new, 1)

old2 = '''            _wait = SYNTH_TIMEOUT_ONLINE if _online_now else SYNTH_TIMEOUT_OFFLINE
'''
new2 = '''            _wait = SYNTH_TIMEOUT_ONLINE if _online_now else SYNTH_TIMEOUT_OFFLINE
            # 刚重启时客户端还没重新 poll 上来，给一点宽限，避免首条请求
            # 被误判为"电脑关机"而立刻回落
            if (_now() - _started_at) < GRACE_SECONDS:
                _wait = max(_wait, GRACE_WAIT)
'''
assert old2 in s, "超时选择处未匹配"
s = s.replace(old2, new2, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("宽限期已加入，语法通过。备份:", bak.rsplit("/", 1)[-1])
print("  宽限期: %ss / 最少等 %ss" % (60, 6))
