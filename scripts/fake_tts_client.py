"""模拟一个本机 GPT-SoVITS 客户端，验证整条链路是否通
真的回传一段音频（洛天依样音片段），用来确认「服务器→代理→客户端→返回」闭环。
"""
import base64
import json
import sys
import threading
import time
import urllib.request

SERVER = "https://game.lingluotoki.dpdns.org/tts"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
AUDIO_FILE = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fake_local.wav"

with open(AUDIO_FILE, "rb") as f:
    AUDIO_B64 = base64.b64encode(f.read()).decode()

H = {"Content-Type": "application/json"}
if TOKEN:
    H["X-TTSToken"] = TOKEN


def post(path, body, timeout=60):
    req = urllib.request.Request(SERVER.rstrip("/") + path,
                                 data=json.dumps(body).encode(), headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


stop = threading.Event()
count = [0]


def loop():
    while not stop.is_set():
        try:
            task = post("/poll", {}, timeout=40)
        except Exception as e:
            print("[poll 失败]", type(e).__name__, str(e)[:80], flush=True)
            time.sleep(3)
            continue
        tid = task.get("task_id")
        if not tid:
            continue
        count[0] += 1
        print("[任务] %s" % task.get("text", "")[:40], flush=True)
        try:
            post("/result", {"task_id": tid, "audio_b64": AUDIO_B64}, timeout=30)
            print("  → 已回传模拟音频 (%.0f KB)" % (len(AUDIO_B64) * 3 / 4 / 1024), flush=True)
        except Exception as e:
            print("  [回传失败]", e, flush=True)


t = threading.Thread(target=loop, daemon=True)
t.start()
print("模拟客户端已启动，等待任务…（Ctrl+C 退出）", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    stop.set()
    print("\n已退出，共处理 %d 个任务" % count[0])
