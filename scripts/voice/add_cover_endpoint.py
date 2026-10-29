"""给 local_tts_server 加「音频转换」任务类型（cover）。

背景：RVC 换音色必须跑在有 GPU 的用户电脑上，容器连不到它。
复用现有通道（任务队列 + 长轮询）比新开端口省事得多：
  · 服务器排队 → 用户 PC 来领 → 做完回传
  · 不新增端口、不改 sshd、不动 bat

协议保持向后兼容
----------------
任务字段新增 `kind`：缺省视为 "tts"（老客户端不受影响）
  {"task_id": "t123", "kind": "tts",   "text": "...", "deadline": 1.7e9}
  {"task_id": "c456", "kind": "cover", "audio_b64": "...", "params": {...}, "deadline": ...}

新增端点
  POST /cover  {"audio_b64": "...", ...任意参数}  →  {"ok":true,"audio_b64":"..."}
               超时/本机离线 → 408；本机报错 → 500

两个护栏
  · 体积上限：base64 超过 LTT_MAX_AUDIO_MB（默认 30MB）直接 413，
    避免大文件把只有 1.5G 可用内存的机器打爆
  · deadline：任务带截止时间，客户端领到过期任务应直接丢弃回报错，
    否则超时后残留在队列里的任务会白占 GPU
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/bin/local_tts_server.py")
bak = "%s.bak-cover-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

assert "/cover" not in s, "已打过 cover 补丁"

# ---------- 1) 常量 ----------
old = 'CACHE_MAX_FILES = int(os.environ.get("LTT_CACHE_MAX_FILES", "400"))\n'
new = ('CACHE_MAX_FILES = int(os.environ.get("LTT_CACHE_MAX_FILES", "400"))\n'
       '\n'
       '# ---- 音频转换（cover）----\n'
       '# 分离 + RVC 比 TTS 慢一个量级，给足时间；离线时仍用 1s 快速回落\n'
       'COVER_TIMEOUT = float(os.environ.get("LTT_COVER_TIMEOUT", "300"))\n'
       '# base64 长度上限（不是原始字节数）。30MB ≈ 22MB 原始音频，够 3 分钟歌曲\n'
       'MAX_AUDIO_B64 = int(float(os.environ.get("LTT_MAX_AUDIO_MB", "30")) * 1024 * 1024)\n')
assert old in s, "缓存常量锚点未匹配"
s = s.replace(old, new, 1)

# ---------- 2) /synth 入队加 kind + deadline ----------
old_q = '            _pending.put({"task_id": tid, "text": text})'
new_q = ('            _pending.put({"task_id": tid, "kind": "tts", "text": text,\n'
         '                          "deadline": _now() + _wait})')
assert old_q in s, "/synth 入队未匹配"
s = s.replace(old_q, new_q, 1)

# 注意：_wait 在入队之后才计算，需要把入队挪到 _wait 之后。
# 原顺序：入队 → 算 _online_now/_wait → ev.wait
# 新顺序：算 _wait → 入队（带 deadline）→ ev.wait
old_seq = '''            _pending.put({"task_id": tid, "kind": "tts", "text": text,
                          "deadline": _now() + _wait})
            # 客户端 30 秒内 poll 过 = 在线 → 给 25s；否则多半关机 → 1s 快速回落
            with _lock:
                _online_now = (_now() - _last_poll) < 30
            _wait = SYNTH_TIMEOUT_ONLINE if _online_now else SYNTH_TIMEOUT_OFFLINE
            # 刚重启时客户端还没重新 poll 上来，给一点宽限，避免首条请求
            # 被误判为"电脑关机"而立刻回落
            if (_now() - _started_at) < GRACE_SECONDS:
                _wait = max(_wait, GRACE_WAIT)
'''
new_seq = '''            # 客户端 30 秒内 poll 过 = 在线 → 给 25s；否则多半关机 → 1s 快速回落
            with _lock:
                _online_now = (_now() - _last_poll) < 30
            _wait = SYNTH_TIMEOUT_ONLINE if _online_now else SYNTH_TIMEOUT_OFFLINE
            # 刚重启时客户端还没重新 poll 上来，给一点宽限，避免首条请求
            # 被误判为"电脑关机"而立刻回落
            if (_now() - _started_at) < GRACE_SECONDS:
                _wait = max(_wait, GRACE_WAIT)
            _pending.put({"task_id": tid, "kind": "tts", "text": text,
                          "deadline": _now() + _wait})
'''
assert old_seq in s, "/synth 顺序段未匹配"
s = s.replace(old_seq, new_seq, 1)

# ---------- 3) 新增 /cover 端点（插在 /synth 之前） ----------
anchor = '        if self.path.startswith("/synth"):'
assert anchor in s, "/synth 分支锚点未匹配"

cover = '''        if self.path.startswith("/cover"):
            audio_b64 = body.get("audio_b64") or ""
            if not audio_b64:
                return self._send(400, {"ok": False, "error": "empty audio"})
            if len(audio_b64) > MAX_AUDIO_B64:
                return self._send(413, {
                    "ok": False,
                    "error": "audio too large (%.1fMB > %dMB)"
                             % (len(audio_b64) / 1048576.0,
                                MAX_AUDIO_B64 // 1048576)})
            params = {k: v for k, v in body.items() if k != "audio_b64"}

            with _lock:
                _online_now = (_now() - _last_poll) < 30
            _wait = COVER_TIMEOUT if _online_now else SYNTH_TIMEOUT_OFFLINE
            if (_now() - _started_at) < GRACE_SECONDS:
                _wait = max(_wait, GRACE_WAIT)

            tid = "c%d_%d" % (int(_now() * 1000), threading.get_ident() % 100000)
            ev = threading.Event()
            with _lock:
                _results[tid] = [ev, None]
            _pending.put({"task_id": tid, "kind": "cover",
                          "audio_b64": audio_b64, "params": params,
                          "deadline": _now() + _wait})

            got = ev.wait(_wait)
            with _lock:
                slot = _results.pop(tid, None)
            if not got or not slot or not slot[1]:
                with _lock:
                    try:
                        _pending.get_nowait()
                    except queue.Empty:
                        pass
                return self._send(408, {"ok": False, "error": "cover unavailable"})
            res = slot[1]
            if res.get("error"):
                return self._send(500, {"ok": False, "error": res.get("error")})
            return self._send(200, {"ok": True, "audio_b64": res.get("audio_b64", "")})

'''
s = s.replace(anchor, cover + anchor, 1)

# ---------- 4) /health 增加对列与超时信息 ----------
old_h = '''                                     "cache": _cache_count(),'''
new_h = '''                                     "cache": _cache_count(),
                                     "cover_timeout": COVER_TIMEOUT,'''
assert old_h in s, "/health 未匹配"
s = s.replace(old_h, new_h, 1)

# ---------- 5) 启动日志提示 cover ----------
old_log = '''    print("[local-tts] 监听 %s:%d（synth 超时：在线 %.0fs / 离线 %.0fs）"
          % (HOST, PORT, SYNTH_TIMEOUT_ONLINE, SYNTH_TIMEOUT_OFFLINE), flush=True)'''
new_log = '''    print("[local-tts] 监听 %s:%d（synth 超时：在线 %.0fs / 离线 %.0fs；"
          "cover 超时 %.0fs，单音频上限 %dMB）"
          % (HOST, PORT, SYNTH_TIMEOUT_ONLINE, SYNTH_TIMEOUT_OFFLINE,
             COVER_TIMEOUT, MAX_AUDIO_B64 // 1048576), flush=True)'''
assert old_log in s, "启动日志未匹配"
s = s.replace(old_log, new_log, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("补丁已应用，语法通过。备份:", bak.rsplit("/", 1)[-1])
for probe, label in (
        ("COVER_TIMEOUT", "cover 超时"),
        ("MAX_AUDIO_B64", "体积上限"),
        ('"/cover"', "新增端点"),
        ('"kind": "tts"', "tts 标记"),
        ('"kind": "cover"', "cover 标记"),
        ('"deadline"', "截止时间"),
        ("cover_timeout", "health 字段")):
    print("  %-10s %s" % (label, "OK" if probe in s else "!! 缺失"))
