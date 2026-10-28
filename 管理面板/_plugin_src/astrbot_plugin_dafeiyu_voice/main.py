"""大肥鱼语音 v2.0 —— 多档回落版（本地 GPT-SoVITS → MiMo → Edge TTS）

借鉴 astrbot_plugin_voice_clone_flow 的思路：
  - 可见文字与语音并存（append）或替换（replace），可配置
  - 长回复按句切分，逐段合成、按序发送
  - 任何一档失败自动降级；全部失败则只保留文字（绝不发错音）
  - 多档回落：本地 GPT-SoVITS(桥接 32000) → MiMo → Edge TTS(免费)

命令：
  /说 <文本>       用洛天依本地音色念指定的话（走回落链）
  /语音开          开启自动语音（之后每条回复转语音）
  /语音关          关闭自动语音
  /语音状态        查看状态
  /语音测试        依次测试各档是否可用
"""
import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    import edge_tts  # 免费 Edge TTS
    _HAS_EDGE = True
except Exception:
    edge_tts = None
    _HAS_EDGE = False

FFMPEG = "/usr/bin/ffmpeg"
STATE_DIR = "/AstrBot/data/plugin_data/astrbot_plugin_dafeiyu_voice"
STATE_FILE = os.path.join(STATE_DIR, "voice_state.json")

_SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]?")


def _split_sentences(text):
    """按句切分（句末标点/换行）。"""
    out = [s.strip() for s in _SENT_RE.findall(text or "") if s.strip()]
    return out or ([text.strip()] if (text or "").strip() else [])


@register(
    "astrbot_plugin_dafeiyu_voice",
    "ops",
    "大肥鱼语音：/说 /语音开 /语音关，多档回落（本地 GPT-SoVITS→MiMo→Edge TTS）",
    "2.0.0",
)
class DafeiyuVoicePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))

        # 档位开关与顺序
        self.local_enabled = bool(cfg.get("local_enabled", True))
        self.mimo_enabled = bool(cfg.get("mimo_enabled", False))
        self.edge_enabled = bool(cfg.get("edge_enabled", True))
        self.tier_order = str(cfg.get("tier_order") or "local,mimo,edge")

        # 本地桥接
        self.proxy_url = str(cfg.get("proxy_url") or "http://172.18.0.1:32000").rstrip("/")
        self.proxy_token = str(cfg.get("proxy_token") or "")
        try:
            self.local_timeout = int(cfg.get("local_timeout", 30))
        except (TypeError, ValueError):
            self.local_timeout = 30

        # MiMo（可选，OpenAI 兼容 /v1/audio/speech）
        self.mimo_api_base = str(cfg.get("mimo_api_base") or "").rstrip("/")
        self.mimo_api_key = str(cfg.get("mimo_api_key") or "")
        self.mimo_model = str(cfg.get("mimo_model") or "mimo-v2.5-tts-voiceclone")
        self.mimo_voice = str(cfg.get("mimo_voice") or "mimo-v2.5-tts-voiceclone")
        try:
            self.mimo_timeout = int(cfg.get("mimo_timeout", 30))
        except (TypeError, ValueError):
            self.mimo_timeout = 30

        # Edge TTS
        self.edge_voice = str(cfg.get("edge_voice") or "zh-CN-XiaoyiNeural")
        self.edge_pitch = str(cfg.get("edge_pitch") or "+15Hz")
        self.edge_rate = str(cfg.get("edge_rate") or "+0%")

        # 输出 / 长度
        self.output_mode = str(cfg.get("output_mode") or "append").lower()  # append|replace
        self.sentence_split = bool(cfg.get("sentence_split", True))
        try:
            self.max_length = int(cfg.get("max_length", 200))
        except (TypeError, ValueError):
            self.max_length = 200
        try:
            self.min_length = int(cfg.get("min_length", 1))
        except (TypeError, ValueError):
            self.min_length = 1
        self.silk_friendly = bool(cfg.get("silk_friendly", True))

        self._voice_on = set()
        self._loaded = False

    async def initialize(self):
        logger.info("[语音] dafeiyu_voice v2.0 已启用（本地→MiMo→Edge 回落；edge=%s）"
                    % _HAS_EDGE)

    # ---------------- 状态 ----------------
    def _load_state(self):
        if self._loaded:
            return
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, encoding="utf-8") as f:
                    d = json.load(f)
                on = d.get("on") if isinstance(d, dict) else None
                if isinstance(on, list):
                    self._voice_on = set(str(x) for x in on)
        except Exception as e:
            logger.warning("[语音] 读取状态失败: %s" % e)
        self._loaded = True

    def _save_state(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"on": sorted(self._voice_on)}, f, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.warning("[语音] 保存状态失败: %s" % e)

    def _arg(self, event, *cmds):
        s = (event.message_str or "").strip()
        for c in cmds:
            for pre in ("/", "／", "", "#"):
                t = pre + c
                if s.startswith(t):
                    return s[len(t):].strip().lstrip("/／").strip()
        return s

    # ---------------- 文本处理 ----------------
    def _sanitize(self, text):
        """本地 GPT-SoVITS 是 GBK，非 GBK 字符（颜文字/emoji/´）会让它 500，这里过滤掉。"""
        out = []
        for ch in text or "":
            try:
                ch.encode("gbk")
            except (UnicodeEncodeError, UnicodeError):
                continue
            out.append(ch)
        return " ".join("".join(out).split())

    # 语音专用清理：只保留 中文+英文数字+常用中英文标点，其余（颜文字符号/假名/希腊/emoji/括号）全删。
    # GBK 过滤不够——颜文字里的 = ( ) ω ﾟ 本身就是 GBK 可编码的，会被留下。
    _KEEP_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9，。！？、；：“”‘’《》…—,.!?\- ]")

    def _clean(self, text):
        t = self._KEEP_RE.sub("", text or "")
        return " ".join(t.split())

    # ---------------- 各档合成 ----------------
    def _bridge_synth(self, text):
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.proxy_token:
            headers["X-TTSToken"] = self.proxy_token
        req = urllib.request.Request(self.proxy_url + "/synth", data=payload,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.local_timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
            return j.get("audio_b64") or ""

    async def _tts_local(self, text):
        t = self._sanitize(text)
        if not t:
            return None, "本地:无可发音内容"
        try:
            b64 = await asyncio.get_event_loop().run_in_executor(None, self._bridge_synth, t)
            if b64:
                return base64.b64decode(b64), None
            return None, "本地:空音频"
        except urllib.error.HTTPError as e:
            if e.code == 408:
                return None, "本地:本机没接单"
            return None, "本地:HTTP %s" % e.code
        except Exception as e:
            return None, "本地:%s" % str(e)[:60]

    async def _tts_mimo(self, text):
        """MiMo TTS：走 /v1/chat/completions，需一条 assistant 消息（内容=要念的文本），
        返回 choices[0].message.audio.data (base64 WAV)。"""
        if not self.mimo_enabled or not self.mimo_api_base:
            return None, "MiMo:未配置"

        def _call():
            payload = json.dumps({
                "model": self.mimo_model,
                "messages": [{"role": "assistant", "content": text}],
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.mimo_api_key:
                headers["Authorization"] = "Bearer " + self.mimo_api_key
            req = urllib.request.Request(self.mimo_api_base + "/chat/completions",
                                         data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.mimo_timeout) as r:
                j = json.loads(r.read().decode("utf-8"))
            au = ((j.get("choices") or [{}])[0].get("message") or {}).get("audio") or {}
            data = au.get("data") or ""
            if not data:
                raise ValueError("响应无 audio")
            return base64.b64decode(data)

        try:
            data = await asyncio.get_event_loop().run_in_executor(None, _call)
            return (data, None) if data else (None, "MiMo:空音频")
        except urllib.error.HTTPError as e:
            return None, "MiMo:HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:80])
        except Exception as e:
            return None, "MiMo:%s" % str(e)[:60]

    async def _tts_edge(self, text):
        if not _HAS_EDGE:
            return None, "Edge:未安装"
        try:
            comm = edge_tts.Communicate(text, self.edge_voice,
                                        rate=self.edge_rate, pitch=self.edge_pitch)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf += chunk["data"]
            if buf:
                return bytes(buf), None
            return None, "Edge:空音频"
        except Exception as e:
            return None, "Edge:%s" % str(e)[:60]

    async def _synthesize(self, text):
        """按 tier_order 依次尝试，返回 (audio_bytes|None, tier, err)。"""
        fns = {"local": self._tts_local, "mimo": self._tts_mimo, "edge": self._tts_edge}
        enabled = {"local": self.local_enabled, "mimo": self.mimo_enabled, "edge": self.edge_enabled}
        errs = []
        for name in [x.strip() for x in self.tier_order.split(",") if x.strip()]:
            fn = fns.get(name)
            if not fn or not enabled.get(name):
                continue
            audio, err = await fn(text)
            if audio:
                return audio, name, None
            if err:
                errs.append(err)
        return None, None, " | ".join(errs) or "无可用档位"

    # ---------------- 发送 ----------------
    def _to_record(self, data):
        tmp = tempfile.mkdtemp(prefix="voice_")
        ext = ".wav" if data[:4] == b"RIFF" else ".mp3"
        src = os.path.join(tmp, "in" + ext)
        with open(src, "wb") as f:
            f.write(data)
        if self.silk_friendly:
            dst = os.path.join(tmp, "v24.mp3")
            try:
                r = subprocess.run(
                    [FFMPEG, "-y", "-loglevel", "error", "-i", src,
                     "-af", "alimiter=limit=0.79",
                     "-ac", "1", "-ar", "24000", "-b:a", "128k", dst],
                    capture_output=True, timeout=60)
                if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 2000:
                    with open(dst, "rb") as f:
                        return Comp.Record.fromBase64(base64.b64encode(f.read()).decode())
            except Exception as e:
                logger.warning("[语音] 24k 预处理失败，按原样发: %s" % e)
        return Comp.Record.fromBase64(base64.b64encode(data).decode())

    async def _build_records(self, text):
        """分句 → 逐段合成 → Record 列表。任何一段全档失败则整体失败（保留文字）。
        注：语音一律用 _clean 清掉颜文字/emoji/符号后再合成（所有档位统一）。"""
        text = self._clean(text)
        if not text:
            return None, "无可发音内容（全是颜文字/表情）", None
        segs = _split_sentences(text) if self.sentence_split else [text]
        segs = [s for s in segs if s.strip()]
        if not segs:
            return None, "空文本", None
        records, used_tier = [], None
        for seg in segs:
            audio, tier, err = await self._synthesize(seg)
            if not audio:
                return None, "分段失败(%s): %s" % (seg[:16], err), used_tier
            used_tier = tier or used_tier
            rec = await asyncio.get_event_loop().run_in_executor(None, self._to_record, audio)
            records.append(rec)
        return records, None, used_tier

    # ---------------- 命令 ----------------
    @filter.command("说", alias={"说话", "朗读", "念", "speak"})
    async def say_cmd(self, event: AstrMessageEvent):
        """用洛天依本地音色念一段话。用法：/说 你好呀"""
        if not self.enabled:
            yield event.plain_result("语音功能没开。")
            return
        text = (self._arg(event, "说", "说话", "朗读", "念", "speak") or "").strip()
        if not text:
            yield event.plain_result("用法：/说 你想让我念的话\n（比如 /说 世界和平）")
            return
        if len(text) > 500:
            text = text[:500]
        records, err, tier = await self._build_records(text)
        if not records:
            yield event.plain_result("语音合成失败：%s" % err)
            return
        yield event.chain_result(records)

    @filter.command("语音开", alias={"开语音", "语音模式开"})
    async def voice_on_cmd(self, event: AstrMessageEvent):
        self._load_state()
        umo = getattr(event, "unified_msg_origin", "") or ""
        self._voice_on.add(umo)
        self._save_state()
        yield event.plain_result("好，语音模式已开——接下来我会用声音回你。发 /语音关 可关闭。")

    @filter.command("语音关", alias={"关语音", "语音模式关"})
    async def voice_off_cmd(self, event: AstrMessageEvent):
        self._load_state()
        umo = getattr(event, "unified_msg_origin", "") or ""
        self._voice_on.discard(umo)
        self._save_state()
        yield event.plain_result("好，语音模式已关——恢复文字回复。")

    @filter.command("语音状态", alias={"状态"})
    async def voice_status_cmd(self, event: AstrMessageEvent):
        self._load_state()
        umo = getattr(event, "unified_msg_origin", "") or ""
        on = umo in self._voice_on
        yield event.plain_result(
            "语音模式：%s\n档位：本地=%s MiMo=%s Edge=%s  顺序=%s\n输出：%s\n"
            "/说 <文本> · /语音开 · /语音关 · /语音模式 只语音|文字加语音 · /语音测试"
            % ("开" if on else "关", self.local_enabled, self.mimo_enabled,
               self.edge_enabled, self.tier_order,
               "只语音" if self.output_mode == "replace" else "文字+语音"))

    @filter.command("语音模式", alias={"输出模式"})
    async def voice_mode_cmd(self, event: AstrMessageEvent):
        """切换自动语音的输出方式：只语音 / 文字加语音。"""
        t = (self._arg(event, "语音模式", "输出模式") or "").strip()
        if ("只" in t or "纯" in t) and "文字" not in t:
            self.output_mode = "replace"
            yield event.plain_result("好，语音模式改成【只发语音】（不发文字）。")
        elif "文字" in t or "加" in t or "一起" in t:
            self.output_mode = "append"
            yield event.plain_result("好，语音模式改成【文字 + 语音一起发】。")
        else:
            yield event.plain_result(
                "当前输出：%s\n用法：/语音模式 只语音　或　/语音模式 文字加语音"
                % ("只语音" if self.output_mode == "replace" else "文字+语音"))

    @filter.command("语音测试")
    async def voice_test_cmd(self, event: AstrMessageEvent):
        """依次测试各档能否出声。"""
        if not self.enabled:
            yield event.plain_result("语音功能没开。")
            return
        lines = []
        test = "测试一二三"
        try:
            _b, _e = await self._tts_local(test)
            lines.append("① 本地 GPT-SoVITS：%s" % ("可用" if _b else "失败 " + str(_e)))
        except Exception as e:
            lines.append("① 本地：失败 %s" % str(e)[:60])
        if self.mimo_enabled:
            _b, _e = await self._tts_mimo(test)
            lines.append("② MiMo：%s" % ("可用" if _b else "失败 " + str(_e)))
        else:
            lines.append("② MiMo：未启用")
        _b, _e = await self._tts_edge(test)
        lines.append("③ Edge TTS：%s" % ("可用" if _b else "失败 " + str(_e)))
        yield event.plain_result("语音各档测试：\n" + "\n".join(lines))

    # ---------------- 自动语音 ----------------
    @filter.on_decorating_result()
    async def auto_voice(self, event: AstrMessageEvent):
        if not self.enabled:
            return
        self._load_state()
        umo = getattr(event, "unified_msg_origin", "") or ""
        if umo not in self._voice_on:
            return
        try:
            result = event.get_result()
            if result is None or not getattr(result, "chain", None):
                return
            try:
                is_llm = result.is_llm_result()
            except Exception:
                is_llm = None
            if is_llm is False:
                return
            # 结果里带文件/图片/语音（如点歌生成的歌）时，绝不替换 chain，
            # 原样发送，避免「只语音」模式把整首歌顶掉、也避免把歌单念一遍。
            _MEDIA = ("File", "Record", "Image", "Video")
            if any(type(c).__name__ in _MEDIA for c in result.chain):
                logger.info("[语音] 结果含媒体(文件/图片/语音)，跳过自动语音，保持原样发送")
                return
            parts = [c.text for c in result.chain if hasattr(c, "text")]
            text = "".join(parts).strip()
            if len(text) < self.min_length:
                return
            logger.info("[语音] auto_voice: umo命中 is_llm=%s chars=%d" % (is_llm, len(text)))
            if len(text) > self.max_length:
                text = text[: self.max_length]
            records, err, tier = await self._build_records(text)
            if not records:
                logger.warning("[语音] 自动语音失败, 保留文字: %s" % err)
                return
            if self.output_mode == "replace":
                # 兜底：非文本组件一律保留，只替换文字部分
                keep = [c for c in result.chain if not hasattr(c, "text")]
                result.chain = keep + records
            else:  # append：清理后的文字 + 语音（语音模式下不生成颜文字）
                new_chain = []
                for c in result.chain:
                    if hasattr(c, "text"):
                        ct = self._clean(c.text)
                        if ct:
                            new_chain.append(Comp.Plain(ct))
                    else:
                        new_chain.append(c)
                result.chain = new_chain + records
            logger.info("[语音] auto_voice: 已加入语音(%d 段, 档位=%s, 模式=%s)"
                        % (len(records), tier, self.output_mode))
        except Exception as e:
            logger.error("[语音] auto_voice 异常: %s" % e)

    async def terminate(self):
        logger.info("[语音] dafeiyu_voice 已停用")
