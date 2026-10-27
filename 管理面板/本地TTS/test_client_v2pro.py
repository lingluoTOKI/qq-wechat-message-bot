"""验证客户端能适配 v2Pro 风格的 API
起一个模拟 GPT-SoVITS（GET query 参数 + 两个独立权重端点），
再用真客户端的 _try_set_weights() / synth() 去打它。
"""
import base64
import http.server
import io
import json
import math
import os
import struct
import sys
import threading
import wave
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------- 生成一个 0.3 秒的测试 wav ----------
def make_wav():
    sr = 24000
    n = int(sr * 0.3)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / sr)))
            for i in range(n))
        w.writeframes(frames)
    return buf.getvalue()


WAV = make_wav()
LOG = []


class MockGPT(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _wav(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(WAV)))
        self.end_headers()
        self.wfile.write(WAV)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        LOG.append(("GET", u.path, sorted(q.keys())))
        if u.path == "/set_gpt_weights":
            if not q.get("weights_path"):
                return self._json(400, {"message": "gpt weight path is required"})
            return self._json(200, {"message": "success"})
        if u.path == "/set_sovits_weights":
            if not q.get("weights_path"):
                return self._json(400, {"message": "sovits weight path is required"})
            return self._json(200, {"message": "success"})
        if u.path == "/tts":
            if not q.get("text"):
                return self._json(400, {"message": "text required"})
            return self._wav()
        return self._json(404, {"message": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        LOG.append(("POST", u.path, sorted(body.keys())))
        if u.path == "/tts":
            if not body.get("text"):
                return self._json(400, {"message": "text required"})
            return self._wav()
        return self._json(404, {"message": "not found"})


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 9988), MockGPT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1)

    import local_tts_client as C
    C.GPT_API = "http://127.0.0.1:9988"
    C.GPT_MODEL_PATH = r"D:\fake\GPT-SoVITS\luotianyi-e50.ckpt"
    C.GPT_CKPT_PATH = r"D:\fake\GPT-SoVITS\pretrained_models\luotianyi_e16_s432.pth"
    C.REF_AUDIO = r"F:\fake\ref.wav"
    C.PROMPT_TEXT = "大家好，我是虚拟歌手洛天依"
    C._load_config = lambda: None   # 别读本地 config.json

    OK = []

    def ck(n, c, d=""):
        OK.append(bool(c))
        print("%s  %-42s %s" % ("PASS" if c else "FAIL", n, d))

    print("=" * 74)
    print("A. 权重加载（应走 v2Pro 的分端点 GET+query）")
    print("=" * 74)
    how, ok_gpt, ok_sovits = C._try_set_weights()
    ck("GPT 权重端点命中", ok_gpt, how)
    ck("SoVITS 权重端点命中", ok_sovits, how)
    print("  实际请求序列：")
    for m, p, keys in LOG:
        print("    %-4s %-24s %s" % (m, p, keys))

    print()
    print("=" * 74)
    print("B. 合成（应走 GET /tts?query）")
    print("=" * 74)
    LOG.clear()
    audio = C.synth("测试一句")
    ck("拿到音频", bool(audio), "长度 %s" % (len(audio) if audio else 0))
    if audio:
        raw = base64.b64decode(audio)
        ck("是合法 WAV", raw[:4] == b"RIFF", "%.1f KB" % (len(raw) / 1024))
    tts_calls = [x for x in LOG if x[1] == "/tts"]
    ck("调用了 /tts", bool(tts_calls), str(tts_calls[:1]))
    ck("传了 ref_audio_path",
       any("ref_audio_path" in (x[2] or []) for x in tts_calls),
       str([x[2] for x in tts_calls]))

    print()
    print("=" * 74)
    print("结果: %d/%d 通过" % (sum(OK), len(OK)))
    print("=" * 74)
    srv.shutdown()
    sys.exit(0 if all(OK) else 1)


import time
main()
