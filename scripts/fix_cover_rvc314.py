"""把客户端 `_cover_gradio` 换成实测通过的协议（RVC20260718 / Gradio 3.14）。

原实现是照 gradio 4/5 写的（gradio_client + /call/{api}），在这套整合包里**全部 404**。
实测结论：
  · 调用：POST /api/{api_name}  body {"data": [...]}  → 同步返回 {"data": [...]}
  · 上传：不需要！audio 组件接受内联 base64
          {"name":"x.wav","data":"data:audio/wav;base64,...","is_file":false}
  · 输出：{"name":"C:\\...\\tmpXX.wav","data":null,"is_file":true}
          因为客户端和 RVC 在同一台机器，直接读这个路径即可

参数签名（从 /config 的 dependencies 解析得到）：
  infer_change_voice(model, index_rate, protect)
  infer_convert(speaker, audio, pitch, f0_method, index_path,
                index_rate, resample_sr, rms_mix_rate, protect)

顺带：不再依赖 gradio_client（RVC 自带 runtime 里没装），保住"零第三方依赖"。
"""
import os
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path(r"F:\github\BOT用\管理面板\本地TTS\local_tts_client.py")
bak = "%s.bak-rvc314-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

start = s.find("def _cover_gradio(")
end = s.find("def do_cover(")
assert start > 0 and end > start, "找不到 _cover_gradio 段"

new_block = '''def _rvc_post(base, path, data, timeout):
    body = json.dumps({"data": data}).encode("utf-8")
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cover_gradio(src_path, params):
    """RVC WebUI（Gradio 3.14）换音色。纯标准库，已在本机实测通过。

    实测数据（RTX 4060 Laptop）：选模型 0.7 秒，5 秒音频转换 26.9 秒
    （其中 F0 提取 19 秒，首次会加载 rmvpe 模型；之后会快很多）。
    """
    base = RVC_API_URL.rstrip("/")
    pitch = int(params.get("pitch", RVC_PITCH))
    index_rate = float(params.get("index_rate", RVC_INDEX_RATE))

    if not os.path.isfile(src_path):
        return None, "源文件不存在: %s" % src_path

    # 1) 确保模型已加载（幂等，很快）
    if RVC_MODEL:
        try:
            _rvc_post(base, "/api/infer_change_voice",
                      [RVC_MODEL, index_rate, 0.33], 120)
        except Exception as e:
            print("    [RVC] 选模型失败（继续尝试转换）: %s" % str(e)[:100], flush=True)

    # 2) 内联 base64 传音频——这套整合包没有可用的 /upload
    with open(src_path, "rb") as f:
        raw = f.read()
    name = os.path.basename(src_path)
    audio = {
        "name": name,
        "data": "data:audio/wav;base64," + base64.b64encode(raw).decode(),
        "is_file": False,
        "orig_name": name,
    }

    try:
        res = _rvc_post(base, "/api/infer_convert", [
            0, audio, pitch, "rmvpe", RVC_INDEX, index_rate, 0, 0.25, 0.33], 900)
    except Exception as e:
        return None, "转换请求失败: %s" % str(e)[:150]

    data = res.get("data") or []
    if data and isinstance(data[0], str):
        info = data[0].replace("\\n", " ")
        print("    [RVC] %s" % info[:120], flush=True)

    for item in data:
        if isinstance(item, dict):
            p = item.get("name") or item.get("path") or ""
            if p and os.path.isfile(p):
                return p, None
            d = item.get("data") or item.get("url") or ""
            if isinstance(d, str) and d.startswith("data:"):
                out = os.path.join(os.path.dirname(src_path), "out.wav")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(d.split(",", 1)[1]))
                return out, None
            if isinstance(d, str) and d.startswith("http"):
                out = os.path.join(os.path.dirname(src_path), "out.wav")
                with urllib.request.urlopen(d, timeout=180) as r, open(out, "wb") as f:
                    f.write(r.read())
                return out, None
        elif isinstance(item, str) and item.lower().endswith((".wav", ".flac", ".mp3", ".ogg")):
            if os.path.isfile(item):
                return item, None

    return None, "转换返回里找不到音频: %s" % (str(data)[:150],)


'''

s = s[:start] + new_block + s[end:]
P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("客户端已改为 Gradio 3.14 协议，语法通过。备份:", bak.rsplit(chr(92), 1)[-1])
for probe, label in (
        ("def _rvc_post", "RVC POST 封装"),
        ('"/api/infer_change_voice"', "选模型端点"),
        ('"/api/infer_convert"', "转换端点"),
        ("is_file", "内联 base64"),
        ("gradio_client", "已移除 gradio_client")):
    hit = probe in s
    ok = (not hit) if label.startswith("已移除") else hit
    print("  %-18s %s" % (label, "OK" if ok else "!!"))
