import py_compile, shutil
from datetime import datetime
from pathlib import Path
P = Path("/opt/bot-stack/bin/local_tts_server.py")
shutil.copy(P, "%s.bak-banner-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S")))
s = P.read_text(encoding="utf-8")
old = '''    print("[local-tts] 监听 %s:%d（synth 超时 %.1fs）" % (HOST, PORT, SYNTH_TIMEOUT), flush=True)'''
new = '''    print("[local-tts] 监听 %s:%d（synth 超时：在线 %.0fs / 离线 %.0fs）"
          % (HOST, PORT, SYNTH_TIMEOUT_ONLINE, SYNTH_TIMEOUT_OFFLINE), flush=True)'''
assert old in s, "启动打印未匹配"
P.write_text(s.replace(old, new, 1), encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("启动日志已改准")
