"""修复 spectrecore 在处理语音消息（Record）时报错。

根因：Record 等非文本组件有 .text 属性但值为 None，
`"".join([i.text ...])` 和 `message_text += comp.text` 都会因 NoneType 抛异常。
现象：每发一条语音，日志出现两条 ERRO（不影响语音发送，但污染日志、且可能影响读空气判定）。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/spectrecore/main.py")
shutil.copy(P, "%s.bak-nonefix-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S")))
s = P.read_text(encoding="utf-8")

# 1) 保存 bot 消息到历史：Record 的 text 为 None 导致 join 失败
old1 = 'message_text = "".join([i.text for i in event._result.chain if hasattr(i, "text")])'
new1 = ('message_text = "".join([(i.text or "") for i in event._result.chain '
        'if hasattr(i, "text")])')
assert old1 in s, "保存历史处未匹配"
s = s.replace(old1, new1, 1)

# 2) 发送前的读空气判定：comp.text 为 None 导致 += 失败
old2 = """                for comp in result.chain:
                    if hasattr(comp, 'text'):
                        message_text += comp.text"""
new2 = """                for comp in result.chain:
                    # 语音/图片等组件的 text 可能是 None，必须兜底
                    if hasattr(comp, 'text'):
                        message_text += (comp.text or "")"""
assert old2 in s, "读空气判定处未匹配"
s = s.replace(old2, new2, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("已修复 2 处 NoneType 兜底，语法通过")
print("  join 兜底:", 'join([(i.text or "")' in s)
print("  += 兜底  :", 'message_text += (comp.text or "")' in s)

# 用桩件复现：模拟 Record（text=None）走这两段逻辑
class Rec:
    def __init__(self):
        self.text = None

chain = [Rec(), Rec()]
try:
    "".join([i.text for i in chain if hasattr(i, "text")])
    print("旧写法：未报错（意外）")
except TypeError as e:
    print("旧写法复现失败:", str(e)[:60])
joined = "".join([(i.text or "") for i in chain if hasattr(i, "text")])
acc = ""
for c in chain:
    if hasattr(c, "text"):
        acc += (c.text or "")
print("新写法结果: join=%r  acc=%r" % (joined, acc))
