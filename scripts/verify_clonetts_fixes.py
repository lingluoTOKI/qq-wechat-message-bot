#!/usr/bin/env python3
"""验证 clonetts 两个 bug 是否真修好（在容器内跑）。

1) MiMo Key：_load_mimo_key 现在应该能读到 xiaomi 的 key
2) MessageChain：能构造并可被 event.send 接受（不再 AttributeError）
"""
import json
import sys
import types

sys.path.insert(0, "/AstrBot")

# ---- 1. MiMo Key ----
CMD = "/AstrBot/data/cmd_config.json"
print("=== 1. MiMo Key 读取 ===")
for enc in ("utf-8", "utf-8-sig"):
    try:
        d = json.load(open(CMD, encoding=enc))
        key = ""
        for s in d.get("provider_sources", []):
            if str(s.get("provider", "")).lower() == "xiaomi" or s.get("id") in ("xiaomi", "xiaomi_stt"):
                k = s.get("key") or s.get("api_key") or ""
                if isinstance(k, list):
                    k = k[0] if k else ""
                if k:
                    key = k
                    break
        print("  %-10s -> 读到 key 长度 %d" % (enc, len(key)))
    except Exception as e:
        print("  %-10s -> 失败 %s: %s" % (enc, type(e).__name__, str(e)[:70]))

# 确认插件源码用的是哪一个
src = open("/AstrBot/data/plugins/astrbot_plugin_clonetts/tts_api/dy_tts_api.py",
           encoding="utf-8").read()
line = [l for l in src.splitlines() if "MIMO_CMD_CONFIG" in l and "open(" in l]
print("  插件源码读取方式:", line[0].strip() if line else "(未找到)")

# ---- 2. MessageChain ----
print()
print("=== 2. MessageChain 构造 ===")
try:
    from astrbot.api.event import MessageChain
    import astrbot.api.message_components as Comp
    import base64

    # 造一个极小的合法 wav（44 字节头）即可验证构造
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + \
          (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + \
          (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + \
          b"data" + (0).to_bytes(4, "little")
    b64 = base64.b64encode(wav).decode()
    chain = MessageChain([Comp.Record.fromBase64(b64)])
    print("  MessageChain([Record]) 构造 OK, 组件数:", len(chain.chain))
except Exception as e:
    print("  失败:", type(e).__name__, str(e)[:150])

# ---- 3. 源码里是否还有 Comp.MessageChain ----
main_src = open("/AstrBot/data/plugins/astrbot_plugin_clonetts/main.py",
                encoding="utf-8").read()
print()
print("=== 3. 源码残留检查 ===")
print("  Comp.MessageChain 残留:", main_src.count("Comp.MessageChain"))
print("  MessageChain 导入:", "MessageChain" in main_src.split("\n")[9:12][0] or
      any("MessageChain" in l and "import" in l for l in main_src.splitlines()[:20]))
