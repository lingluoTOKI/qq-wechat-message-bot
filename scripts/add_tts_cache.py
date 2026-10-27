"""给 local_tts_server.py 加音频缓存（借用官方 GPT_SoVITS 插件的思路）。

思路来源：`astrbot_plugin_GPT_SoVITS/core/local_data.py`
  sha256(标准化后的参数 JSON)[:16] + 过期时间 → 音频文件

为什么放在**服务器端**而不是客户端：
  1. 命中直接返回，不派任务 → 省一次长轮询往返
  2. **电脑关机时，之前合成过的句子仍然能播**（这是最大价值）
  3. 单点，所有请求都经过它，缓存利用率最高

缓存键 = sha1(CACHE_TAG + "\\0" + text)
  CACHE_TAG 换了（比如换音色/换模型）→ 旧缓存整体失效，不会串音。
"""
import os
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/bin/local_tts_server.py")
bak = "%s.bak-cache-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

# ---------- 1) 头部：新增缓存常量与工具函数 ----------
old_imp = "import base64\nimport json\nimport os\nimport queue\nimport threading\nimport time\n"
new_imp = ("import base64\nimport hashlib\nimport json\nimport os\nimport queue\n"
           "import threading\nimport time\n")
assert old_imp in s, "import 段未匹配"
s = s.replace(old_imp, new_imp, 1)

old_cfg = '''SYNTH_TIMEOUT_OFFLINE = float(os.environ.get("LTT_SYNTH_TIMEOUT_OFFLINE", "1"))
'''
new_cfg = '''SYNTH_TIMEOUT_OFFLINE = float(os.environ.get("LTT_SYNTH_TIMEOUT_OFFLINE", "1"))

# ---- 音频缓存 ----
# 思路同官方 GPT_SoVITS 插件：同文本同音色只合成一次。
# 放服务器端的额外好处：电脑关机时，已合成过的句子照样能播。
CACHE_ENABLED = os.environ.get("LTT_CACHE_ENABLED", "1") != "0"
CACHE_DIR = os.environ.get("LTT_CACHE_DIR", "/opt/bot-stack/audio-cache")
CACHE_TAG = os.environ.get("LTT_CACHE_TAG", "luotianyi13-v1")
CACHE_MAX_FILES = int(os.environ.get("LTT_CACHE_MAX_FILES", "400"))
_cache_ok = False
_cache_lock = threading.Lock()


def _cache_key(text):
    h = hashlib.sha1((CACHE_TAG + "\\0" + text).encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, h[:32] + ".b64")


def _cache_get(text):
    if not CACHE_ENABLED or not _cache_ok:
        return None
    try:
        with open(_cache_key(text), "r", encoding="utf-8") as f:
            data = f.read().strip()
        if data:
            os.utime(_cache_key(text), None)   # 触碰 mtime，LRU 用
            return data
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return None


def _cache_put(text, b64):
    if not CACHE_ENABLED or not _cache_ok or not b64:
        return
    path = _cache_key(text)
    try:
        with _cache_lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(b64)
            os.replace(tmp, path)          # 原子写，避免半截文件
            _cache_evict()
    except Exception:
        pass


def _cache_evict():
    """超过上限就按 mtime 删最旧的。"""
    try:
        files = [os.path.join(CACHE_DIR, n) for n in os.listdir(CACHE_DIR)
                 if n.endswith(".b64")]
    except Exception:
        return
    over = len(files) - CACHE_MAX_FILES
    if over <= 0:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for p in files[:over]:
        try:
            os.remove(p)
        except Exception:
            pass


def _cache_count():
    if not _cache_ok:
        return 0
    try:
        return sum(1 for n in os.listdir(CACHE_DIR) if n.endswith(".b64"))
    except Exception:
        return 0
'''
assert old_cfg in s, "超时常量段未匹配"
s = s.replace(old_cfg, new_cfg, 1)

# ---------- 2) /synth：先查缓存 ----------
old_synth = '''            tid = "t%d_%d" % (int(_now() * 1000), threading.get_ident() % 100000)'''
new_synth = '''            # ① 缓存命中：直接返回，不派任务（电脑关机也能播）
            _hit = _cache_get(text)
            if _hit:
                return self._send(200, {"ok": True, "audio_b64": _hit,
                                        "cached": True})
            # ② 未命中：派给本机合成
            tid = "t%d_%d" % (int(_now() * 1000), threading.get_ident() % 100000)'''
assert old_synth in s, "/synth 派任务处未匹配"
s = s.replace(old_synth, new_synth, 1)

old_ret = '''            res = slot[1]
            if res.get("error"):
                return self._send(500, {"ok": False, "error": res.get("error")})
            return self._send(200, {"ok": True, "audio_b64": res.get("audio_b64", "")})'''
new_ret = '''            res = slot[1]
            if res.get("error"):
                return self._send(500, {"ok": False, "error": res.get("error")})
            _audio = res.get("audio_b64", "")
            _cache_put(text, _audio)      # 合成成功就落盘
            return self._send(200, {"ok": True, "audio_b64": _audio,
                                    "cached": False})'''
assert old_ret in s, "/synth 返回处未匹配"
s = s.replace(old_ret, new_ret, 1)

# ---------- 3) /health 暴露缓存数量 ----------
old_h = '''            return self._send(200, {"ok": True, "online": online,
                                     "pending": _pending.qsize(),
                                     "time": _now()})'''
new_h = '''            return self._send(200, {"ok": True, "online": online,
                                     "pending": _pending.qsize(),
                                     "cache": _cache_count(),
                                     "time": _now()})'''
assert old_h in s, "/health 未匹配"
s = s.replace(old_h, new_h, 1)

# ---------- 4) main() 初始化缓存目录 ----------
old_main = '''def main():
    if not TOKEN:
        print("[local-tts] 警告：LTT_TOKEN 未设置，接口无鉴权！", flush=True)'''
new_main = '''def main():
    global _cache_ok
    if not TOKEN:
        print("[local-tts] 警告：LTT_TOKEN 未设置，接口无鉴权！", flush=True)
    if CACHE_ENABLED:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            _cache_ok = True
            print("[local-tts] 音频缓存：%s（上限 %d 条，tag=%s，现有 %d 条）"
                  % (CACHE_DIR, CACHE_MAX_FILES, CACHE_TAG, _cache_count()),
                  flush=True)
        except Exception as e:
            print("[local-tts] 音频缓存不可用: %s" % e, flush=True)'''
assert old_main in s, "main() 未匹配"
s = s.replace(old_main, new_main, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("补丁已应用，语法通过。备份:", os.path.basename(bak))
for probe, label in (("def _cache_key", "缓存键"),
                     ('"cached": True', "命中返回"),
                     ("_cache_put(text, _audio)", "写入"),
                     ("_cache_count()", "计数")):
    print("  %-10s %s" % (label, "OK" if probe in s else "!! 缺失"))
