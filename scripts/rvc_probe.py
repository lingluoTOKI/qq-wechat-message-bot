"""RVC WebUI（Gradio 3.14）换音色验证 —— 纯标准库。

协议要点（和 gradio 4/5 完全不同，是踩过的坑）：
  · 调用：POST /api/{api_name}   body {"data": [...]}   → 同步返回 {"data": [...]}
  · 上传：POST /upload           multipart，字段名 files → ["/tmp/gradio/xxx/name.wav"]
  · 没有 /gradio_api 前缀，没有 /call/{api}，没有 /info（4/5 那套在这里全 404）

参数签名（从 /config 的 dependencies 解析得到，不靠猜）：
  infer_change_voice(model_name, index_rate, protect)
  infer_convert(speaker_id, audio, pitch, f0_method, index_path,
                index_rate, resample_sr, rms_mix_rate, protect)
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7899"
SRC = sys.argv[2] if len(sys.argv) > 2 else r"F:\github\BOT用\voicetest\lty\s1_aoyao.wav"
OUT = sys.argv[3] if len(sys.argv) > 3 else r"F:\github\BOT用\voicetest\lty\rvc_out.wav"
MODEL = sys.argv[4] if len(sys.argv) > 4 else "LuoTianyi-test.pth"
INDEX = "logs/LuoTianyi-test.index"
PITCH = int(sys.argv[5]) if len(sys.argv) > 5 else 0


def post_json(path, payload, timeout=600):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def upload(path):
    boundary = "----rvc" + uuid.uuid4().hex
    fn = os.path.basename(path)
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += ('Content-Disposition: form-data; name="files"; filename="%s"\r\n' % fn).encode()
    body += ("Content-Type: %s\r\n\r\n" % ctype).encode()
    body += content + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        BASE + "/upload", data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=300) as r:
        files = json.loads(r.read().decode())
    return files[0]


def save_audio(item, out_path):
    """gradio 3.x 的 audio 输出可能是 {name,data,is_file} 或 {name,url}"""
    if isinstance(item, str):
        if item.startswith("data:"):
            raw = base64.b64decode(item.split(",", 1)[1])
        elif os.path.isfile(item):
            raw = open(item, "rb").read()
        else:
            raise RuntimeError("无法识别的音频输出: %s" % item[:80])
    elif isinstance(item, dict):
        name = item.get("name") or item.get("path") or ""
        if name and os.path.isfile(name):
            raw = open(name, "rb").read()
        else:
            d = item.get("data") or item.get("url") or ""
            if isinstance(d, str) and d.startswith("data:"):
                raw = base64.b64decode(d.split(",", 1)[1])
            elif isinstance(d, str) and d.startswith("http"):
                with urllib.request.urlopen(d, timeout=180) as r:
                    raw = r.read()
            else:
                raise RuntimeError("音频字段无法解析: %r" % (str(item)[:120],))
    else:
        raise RuntimeError("音频类型不支持: %s" % type(item))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(raw)
    return len(raw)


print("=" * 62)
print("RVC 换音色验证  base=%s" % BASE)
print("  模型: %s" % MODEL)
print("  输入: %s" % SRC)
print("=" * 62)

t0 = time.time()
r = post_json("/api/infer_change_voice", {"data": [MODEL, 0.75, 0.33]})
print("[1/3] 选模型 OK（%.1f 秒）反馈 %d 项" % (time.time() - t0, len(r.get("data") or [])))

t0 = time.time()
up = upload(SRC)
print("[2/3] 上传 OK（%.1f 秒）→ %s" % (time.time() - t0, up))

t0 = time.time()
res = post_json("/api/infer_convert", {"data": [
    0, up, PITCH, "rmvpe", INDEX, 0.75, 0, 0.25, 0.33]}, timeout=900)
dt = time.time() - t0
data = res.get("data") or []
print("[3/3] 转换 OK（%.1f 秒）返回 %d 项" % (dt, len(data)))
for i, x in enumerate(data):
    print("   [%d] %s" % (i, str(x)[:120]))

audio = None
for x in data:
    if isinstance(x, dict) and (x.get("data") or x.get("url") or x.get("name")):
        audio = x
    elif isinstance(x, str) and any(x.lower().endswith(e) for e in (".wav", ".flac", ".mp3", ".ogg")):
        audio = x
if audio is None:
    print("!! 没在返回里找到音频")
    sys.exit(1)

n = save_audio(audio, OUT)
print("\n已保存 %s（%.0f KB）" % (OUT, n / 1024))
print("转换耗时 %.1f 秒" % dt)
if n < 5000:
    print("!! 文件过小，可能失败")
    sys.exit(1)
print("成功 OK  去听一下音色")
