import py_compile, shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/tts_api/dy_tts_api.py")
shutil.copy(P, "%s.bak-kaobrk-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S")))
s = P.read_text(encoding="utf-8")

old = '_KEEP = r"\\u4e00-\\u9fffA-Za-z0-9。！？!?，,、；;：:.…~～\\"\'）)】」》"'
new = '_KEEP = r"\\u4e00-\\u9fffA-Za-z0-9。！？!?，,、；;：:.…~～\\"\'（）()【】「」《》〈〉"'
assert old in s, "KEEP 定义未匹配"
s = s.replace(old, new, 1)
P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)
print("保留集已补全左右括号，语法通过")
