import inspect, sys
sys.path.insert(0, "/AstrBot/data/plugins")
import astrbot_plugin_dafeiyu_cover.main as m
from astrbot.core.star.star_handler import star_handlers_registry

OK = []
def ck(n, c, e=""):
    OK.append(bool(c)); print("%s  %-28s %s" % ("PASS" if c else "FAIL", n, e))

ck("模块导入", True)
ck("有 cover_cmd", hasattr(m.DafeiyuCoverPlugin, "cover_cmd"))
ck("有 on_audio", hasattr(m.DafeiyuCoverPlugin, "on_audio"))
ck("cover_cmd 是异步生成器", inspect.isasyncgenfunction(m.DafeiyuCoverPlugin.cover_cmd))

hs = [getattr(h, "handler_name", "") for h in star_handlers_registry]
ck("注册了 cover_cmd", "cover_cmd" in hs)
ck("注册了 on_audio", "on_audio" in hs)
print("     相关 handler:", [h for h in hs if "cover" in h or "audio" in h])

# 构造实例检查配置读取与工具函数
inst = object.__new__(m.DafeiyuCoverPlugin)
inst.enabled = True
inst.proxy_url = "http://172.18.0.1:32000"
inst.proxy_token = "x" * 43
inst.timeout = 320
inst.max_seconds = 60
inst.wait_ttl = 180
inst._waiting = {}
inst._handled = {}
ck("ffmpeg 路径存在", __import__("os").path.isfile(m.FFMPEG), m.FFMPEG)
ck("ffprobe 路径存在", __import__("os").path.isfile(m.FFPROBE), m.FFPROBE)

# file:// URI 解析
import os, tempfile
t = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); t.write(b"x"*3000); t.close()
class C:
    path = None; file = None; file_ = None
    url = "file://" + t.name.replace(os.sep, "/")
comp = C()
p, err = inst._resolve_path(comp)
ck("file:// URI 能解析", p == t.name, "%r" % (p,))
os.unlink(t.name)

# 找音频组件
class Ev:
    class message_obj:
        message = []
ck("无音频时返回 None", inst._find_audio(Ev()) is None)

print()
print("结果: %d/%d" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
