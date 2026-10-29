"""给 clonetts 加 `/说` 命令：直接合成语音，不经过大模型。

借的是官方 GPT_SoVITS 插件的思路（`@filter.command("说")` 直连 TTS），
但我们的版本多做了三件事：
  1. 复用 `_split_tts_parts` → 长文本自动切 2~4 条连着发
  2. 复用 `tts_http_stream` → 自动带颜文字清洗 + 本地/MiMo/edge 三层回落
  3. 失败时明确回一句文字，不让用户干等

价值：**0 token**。原来的「用语音说 xxx」要先过一次模型（约 ¥0.001），
这个命令直接把文字丢给 TTS。
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/main.py")
bak = "%s.bak-say-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

anchor = "    def probability(self, percent) -> bool:"
assert anchor in s, "插入锚点未匹配"
assert '@filter.command("说"' not in s, "已存在 /说 命令，跳过"

cmd = '''    @filter.command("说", alias={"say"})
    async def say_cmd(self, event: AstrMessageEvent):
        """直接合成语音，不经过大模型（不消耗 token）。用法：/说 要说的话"""
        try:
            if not self.enable_tts:
                yield event.plain_result("语音功能没开呢")
                return

            msg = (getattr(event, "message_str", "") or "").strip()
            seg = msg.split(None, 1)
            text = seg[1].strip() if len(seg) > 1 else ""
            if not text:
                yield event.plain_result(
                    "用法：/说 要说的话（直接合成，不经过模型，不花 token）")
                return

            # 太长会合成很久，截断保护
            if len(text) > 300:
                text = text[:300]

            pieces = (_split_tts_parts(text, self.tts_max_parts)
                      if self.tts_split_sentences else [text])

            for i, p in enumerate(pieces):
                if i:
                    await asyncio.sleep(self.tts_part_interval)
                b64 = await tts_http_stream(self, p, "")
                if not b64:
                    yield event.plain_result("语音合成失败了（本机可能离线）")
                    return
                yield event.chain_result([Comp.Record.fromBase64(b64)])
        except Exception as e:
            logger.error(f"[/说] 合成失败: {e}")
            yield event.plain_result(f"语音出错：{e}")

'''

s = s.replace(anchor, cmd + anchor, 1)
P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("已加入 /说 命令，语法通过。备份:", bak.rsplit("/", 1)[-1])
print("  命令定义:", '@filter.command("说", alias={"say"})' in s)
print("  锚点仍在:", anchor in s)

# 静态自检：命令体用到的名字是否都已导入/定义
need = ["asyncio", "Comp.Record", "_split_tts_parts", "tts_http_stream",
        "event.plain_result", "event.chain_result", "self.tts_part_interval",
        "self.tts_split_sentences", "self.tts_max_parts", "self.enable_tts"]
missing = [n for n in need if n.split(".")[0] not in s]
print("  依赖缺失:", missing or "无")
