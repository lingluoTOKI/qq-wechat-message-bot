import astrbot.api.message_components as C
print("message_components 有 MessageChain:", hasattr(C, "MessageChain"))
pub = [x for x in dir(C) if not x.startswith("_")]
print("  公开符号:", pub[:20])

try:
    from astrbot.api.event import MessageChain as MC1
    print("astrbot.api.event.MessageChain ->", MC1)
except Exception as e:
    print("astrbot.api.event 无 MessageChain:", type(e).__name__, e)

try:
    from astrbot.core.message.components import MessageChain as MC2
    print("astrbot.core.message.components.MessageChain ->", MC2)
except Exception as e:
    print("core.message.components 无:", type(e).__name__, e)

# 顺带确认 Record 在哪
print("Comp.Record 存在:", hasattr(C, "Record"))
