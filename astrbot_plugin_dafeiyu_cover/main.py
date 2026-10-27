"""大肥鱼翻唱：把一段音频 / 一首网上的歌换成洛天依的音色（RVC）。

为什么不在这个插件里做推理
--------------------------
RVC 必须有 GPU，服务器只有 2 核 / 1.5G 可用内存，跑不了。
所以推理在用户的电脑上，插件只负责**编排**：

    搜歌/取音频 → 清理+限长 → POST /cover → 拿回音频 → 发到群
                      ↓
             服务器代理排队 → 用户电脑长轮询领走 → 分离+RVC 转换 → 回传

这套通道（隧道 + 队列 + 鉴权 + 超时）已经跑通，复用它，不新开端口。

三种触发方式
------------
/点歌 起风了        → 搜网易云，列表选号，自动下载后翻唱（全自动）
/翻唱               → 进入“等音频”状态（180 秒内发音频即可）
/翻唱 + 同条带音频  → 直接处理
直接发音频（在等音频状态下） → 处理

音源说明
--------
用网易云官方 music.163.com/api/search/get 搜索，外链
/song/media/outer/url 取流。免费曲可直接下；VIP 曲会 302 到 404，
这时自动在搜索结果里找一个同名的免费版本顶上。
"""
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SEARCH_API = "https://music.163.com/api/search/get"
OUTER_URL = "https://music.163.com/song/media/outer/url?id=%s.mp3"

# B 站兜底：直接复用「我想听歌！」插件已经跑通的 B 站客户端
# （它带 wbi 签名和登录 cookie；没登录过就会 412，所以要有降级）
BILI_PLUGIN_DIR = "/AstrBot/data/plugins/astrbot_plugin_listen_music"
BILI_ACCOUNTS = "/AstrBot/data/plugin_data/astrbot_plugin_listen_music/accounts.json"
BILI_BAD_WORDS = ("直播", "教程", "教学", "鬼畜", "reaction", "解说", "纯音乐",
                  "伴奏", "钢琴", "吉他", "和弦", "翻跳", "舞蹈", "MV解析")


@register(
    "astrbot_plugin_dafeiyu_cover",
    "ops",
    "AI 翻唱：/点歌 搜歌自动翻唱，或直接发音频换音色（推理在用户电脑，服务器只做编排）",
    "1.1.0",
)
class DafeiyuCoverPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.proxy_url = str(cfg.get("proxy_url") or "http://172.18.0.1:32000").rstrip("/")
        self.proxy_token = str(cfg.get("proxy_token") or "")
        self.timeout = int(cfg.get("timeout", 700))
        self.max_seconds = int(cfg.get("max_seconds", 90))
        self.song_seconds = int(cfg.get("song_seconds", 240))
        self.wait_ttl = int(cfg.get("wait_ttl", 180))
        self.pick_ttl = int(cfg.get("pick_ttl", 120))
        self.max_mb = int(cfg.get("max_mb", 40))
        self.pitch = int(cfg.get("pitch", 0))
        self.enable_bili = bool(cfg.get("enable_bili", True))
        # 语音 vs 文件：QQ 官方语音(file_type=3, silk)软限制 20MB，
        # 文档未限时长，所以整首歌也能发语音条，不必降级成文件。
        self.record_max_seconds = int(cfg.get("record_max_seconds", 300))
        self.record_max_mb = int(cfg.get("record_max_mb", 15))
        self.enable_search = bool(cfg.get("enable_search", True))
        # 发送方式：auto=按时长自动 / voice=尽量发语音 / file=发文件
        # 命令里带「语音」「文件」可临时覆盖，/发送方式 可改默认（重启后回到配置值）
        self.send_mode = str(cfg.get("send_mode") or "auto").lower()
        # 音质参数：透传给本机，改这里就能调，不用动电脑上的客户端

        self.out_tp = float(cfg.get("out_tp", -1.5))        # 混音真峰值 dBTP
        self.out_kbps = int(cfg.get("out_kbps", 256))       # 成品 mp3 码率
        # 和声：分离模型会把和声留在伴奏轨，等于和声还是原唱的嗓子。
        # 开起来本机就多拆一遍，和声也一起换成洛天依（慢约 1 分钟）。
        self.harmony = bool(cfg.get("harmony", True))
        # 清辅音保护。注意语义是反的：protect<0.5 才启用，0.5 = 完全不保护。
        # 官方默认 0.33；0.25 更稳但换声略淡。
        self.protect = float(cfg.get("protect", 0.33))
        # 后处理重采样，0=不重采样（本版 RVC 已无 filter_radius 这个参数）
        self.resample_sr = int(cfg.get("resample_sr", 0))
        self.f0_method = str(cfg.get("f0_method", "rmvpe"))
        self.rms_mix_rate = float(cfg.get("rms_mix_rate", 0.25))
        # 检索比例：决定"多像洛天依"。实测 0.75 会把音高一起带偏（发飘/怪音），
        # 0.40 是听感最稳的一档（A/B 由用户盲听选定）
        self.index_rate = float(cfg.get("index_rate", 0.4))
        # 双预设（来自本机 rvc_presets.json）：
        #   A = 稳定难句预设（兜底），B = 高质量音色预设（平稳段）
        # 留空 = 用上面这些手调参数；填了则以预设为准（预设里的值优先级更高）
        self.preset = str(cfg.get("preset", "") or "").strip().upper()
        self._waiting = {}    # umo -> 截止时间（等音频）
        self._wait_mode = {}  # umo -> "voice"/"file"（等音频时的临时选择）
        self._pending = {}    # umo -> {"songs": [...], "expire": ts, "uid": "...", "mode": ...}
        self._handled = {}    # message_id -> 时间（防止两个钩子重复处理）

    # 语音/文件的说法，末尾匹配，可叠加
    _SEND_WORDS = (
        ("file", ("文件", "file", "mp3", "音频", "音质", "发文件", "文件版")),
        ("voice", ("语音", "voice", "语音条", "语音版", "发语音")),
    )

    def _parse_mode(self, text):
        """从命令参数末尾剥出『发文件 / 发语音』意图。

        返回 (去掉关键词后的文本, mode)；mode 为 None 表示没指定，按配置走。
        """
        s = (text or "").strip()
        mode = None
        changed = True
        while changed:
            changed = False
            low = s.lower()
            for m, words in self._SEND_WORDS:
                for w in words:
                    if low.endswith(w) and len(s) > len(w):
                        s = s[:-len(w)].strip()
                        mode, changed = m, True
                        break
                if changed:
                    break
        return s, mode

    def _decide_mode(self, mode):
        """命令没指定时，套用 send_mode 配置。"""
        if mode:
            return mode
        return self.send_mode if self.send_mode in ("voice", "file") else None

    # ---------------- 工具 ----------------
    def _mark(self, event):
        """标记这条消息已处理，返回 False 表示重复。"""
        mid = ""
        try:
            mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        except Exception:
            pass
        if not mid:
            return True
        now = time.time()
        for k in [k for k, v in self._handled.items() if now - v > 600]:
            self._handled.pop(k, None)
        if mid in self._handled:
            return False
        self._handled[mid] = now
        return True

    def _find_audio(self, event):
        chain = getattr(getattr(event, "message_obj", None), "message", None) or []
        for comp in chain:
            if isinstance(comp, (Comp.Record, Comp.File)):
                return comp
        return None

    def _arg(self, event, *cmds):
        """从消息里剥掉命令词，拿后面的参数。"""
        s = (event.message_str or "").strip()
        for c in cmds:
            for pre in ("/", "／", "", "#"):
                t = pre + c
                if s.startswith(t):
                    s = s[len(t):].strip()
                    return s.lstrip("/／").strip()
        return s

    def _resolve_path(self, comp):
        """把组件解析成本地文件路径（必要时下载）。"""
        cands = []
        for attr in ("path", "file", "file_", "url"):
            try:
                v = getattr(comp, attr, None)
            except Exception:
                v = None
            if isinstance(v, str) and v:
                cands.append(v)

        for v in cands:
            p = v
            if p.startswith("file://"):
                p = urllib.parse.unquote(p[7:])
                if p.startswith("/") and len(p) > 3 and p[2] == ":":
                    p = p[1:]          # file:///C:/... → C:/...
            if os.path.isfile(p):
                return p, None

        for v in cands:
            if v.startswith("http://") or v.startswith("https://"):
                try:
                    d = tempfile.mkdtemp(prefix="cover_dl_")
                    dst = os.path.join(d, "in.bin")
                    with urllib.request.urlopen(v, timeout=60) as r, open(dst, "wb") as f:
                        shutil.copyfileobj(r, f)
                    return dst, None
                except Exception as e:
                    return None, "下载音频失败: %s" % str(e)[:100]
        return None, "拿不到音频文件（组件字段: %s）" % (cands[:3],)

    def _ff(self, cmd, timeout=180):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            return r.returncode, (r.stderr or b"")[-300:].decode("utf-8", "replace")
        except Exception as e:
            return -1, "%s: %s" % (type(e).__name__, str(e)[:120])

    def _dur(self, path):
        try:
            r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=nw=1:nk=1", path],
                               capture_output=True, timeout=30)
            v = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            return float(v[-1]) if v else 0.0
        except Exception:
            return 0.0

    def _post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.proxy_token:
            headers["X-TTSToken"] = self.proxy_token
        req = urllib.request.Request(self.proxy_url + "/cover", data=data,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # ---------------- 搜歌 ----------------
    def _search(self, keyword, limit=8):
        """返回 [{id,name,artists,duration,free}]，失败抛异常。"""
        body = urllib.parse.urlencode(
            {"s": keyword, "type": 1, "limit": limit, "offset": 0}).encode()
        req = urllib.request.Request(
            SEARCH_API, data=body,
            headers={"User-Agent": UA, "Referer": "https://music.163.com/",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        songs = (j.get("result") or {}).get("songs") or []
        out = []
        for s in songs:
            out.append({
                "id": s.get("id"),
                "name": s.get("name") or "?",
                "artists": "、".join(a.get("name", "") for a in (s.get("artists") or [])),
                "duration": int(s.get("duration") or 0),
                "free": s.get("fee") == 0,
            })
        # 免费的排前面，方便直接点
        out.sort(key=lambda x: (not x["free"],))
        return out

    def _download(self, song_id, dest_dir):
        """下载到 dest_dir，返回 (path, err)。"""
        url = OUTER_URL % song_id
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
            with urllib.request.urlopen(req, timeout=60) as r:
                ctype = (r.headers.get("Content-Type") or "").lower()
                final = r.geturl() or ""
                raw = r.read(self.max_mb * 1048576 + 1)
        except Exception as e:
            return None, "下载失败: %s" % str(e)[:100]

        if len(raw) > self.max_mb * 1048576:
            return None, "歌曲文件超过 %dMB" % self.max_mb
        if "/404" in final or "text/html" in ctype or len(raw) < 40000:
            return None, "VIP"
        ext = ".mp3"
        if "flac" in ctype:
            ext = ".flac"
        elif "mp4" in ctype or "m4a" in ctype:
            ext = ".m4a"
        p = os.path.join(dest_dir, "song" + ext)
        with open(p, "wb") as f:
            f.write(raw)
        return p, None

    # ---------------- B 站兜底 ----------------
    @staticmethod
    def _bili_cookies():
        try:
            with open(BILI_ACCOUNTS, encoding="utf-8") as f:
                raw = json.load(f)
            return (raw.get("bilibili") or {}).get("cookies") or {}
        except Exception:
            return {}

    async def _bili_fetch(self, keyword, want_sec, out_dir):
        """去 B 站搜同名音频并下载。返回 (path, 标题) 或 (None, 原因)。

        网易云的 VIP 曲拿不到直链时走这里——B 站上基本什么歌都有，
        代价是搜出来是视频，得靠标题关键词和时长挑一下。
        """
        if not self.enable_bili:
            return None, "B站兜底没开"
        if not os.path.isdir(BILI_PLUGIN_DIR):
            return None, "没装「我想听歌！」插件"
        if BILI_PLUGIN_DIR not in sys.path:
            sys.path.insert(0, BILI_PLUGIN_DIR)
        try:
            import aiohttp
            from core.bilibili import BilibiliClient
        except Exception as e:
            return None, "B站模块不可用：%s" % str(e)[:80]

        cookies = self._bili_cookies()
        if not cookies:
            return None, "B站没登录（到「我想听歌！」里扫码登录一次）"

        head = (keyword.split() or [keyword])[0]
        key = re.sub(r"\s+", "", head)

        try:
            async with aiohttp.ClientSession() as sess:
                client = BilibiliClient(sess, lambda: cookies)
                hits = await client.search_videos(keyword, limit=10)
                if not hits:
                    return None, "B站没搜到"

                clean = [h for h in hits
                         if not any(w in (h.title or "").lower() for w in BILI_BAD_WORDS)]
                cands = (clean or list(hits))[:5]

                scored = []
                for h in cands:
                    try:
                        info = await client.get_video(h.bvid)
                    except Exception as e:
                        logger.debug("[翻唱] B站取详情失败 %s: %s" % (h.bvid, e))
                        continue
                    pages = getattr(info, "pages", None) or []
                    if not pages:
                        continue
                    dur = (pages[0].duration_ms or 0) / 1000.0
                    if dur <= 0 or dur > self.song_seconds + 180:
                        continue
                    score = 0.0
                    if key and key in re.sub(r"\s+", "", info.title or ""):
                        score += 100
                    if want_sec:
                        score -= abs(dur - want_sec) / 8.0
                    scored.append((score, info, pages[0], dur))

                if not scored:
                    return None, "B站搜到的都对不上"

                for score, info, page, dur in sorted(scored, key=lambda x: -x[0])[:2]:
                    try:
                        a = await client.resolve_audio(info.bvid, page.cid)
                        async with sess.get(a.url, headers=a.headers) as r:
                            if r.status != 200:
                                logger.warning("[翻唱] B站流 HTTP %s" % r.status)
                                continue
                            data = await r.read()
                        if len(data) < 50000:
                            continue
                        p = os.path.join(out_dir, "bili.m4s")
                        with open(p, "wb") as f:
                            f.write(data)
                        logger.info("[翻唱] B站音源 %s | %s | %.0f秒 | %.1fMB"
                                    % (info.bvid, (info.title or "")[:36],
                                       dur, len(data) / 1048576))
                        return p, info.title or ""
                    except Exception as e:
                        logger.warning("[翻唱] B站下载失败 %s: %s" % (info.bvid, str(e)[:100]))
                return None, "B站音频都下载失败"
        except Exception as e:
            return None, "B站搜索失败：%s" % str(e)[:100]

    def _prepare(self, src, out_dir, max_sec):
        """只裁时长，尽量不重编码。

        以前统一压成单声道 128k mp3，等于在网易云/ B 站源文件上又压了一道：
        鼓点瞬态被 mp3 压出振铃，而且立体声被合并后 UVR 更难分清人声和镲片，
        残留的鼓点进了 RVC 就变成"爆破"杂音。现在优先 -c copy 零损失裁剪，
        拷贝不了才退回 256k 立体声。
        """
        ext = os.path.splitext(src)[1] or ".mp3"
        dst = os.path.join(out_dir, "send" + ext)
        rc, err = self._ff([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                            "-t", str(max_sec), "-c", "copy", dst], timeout=180)
        if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 5000:
            dst = os.path.join(out_dir, "send.mp3")
            rc, err = self._ff([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                                "-t", str(max_sec), "-ac", "2", "-ar", "44100",
                                "-b:a", "256k", dst], timeout=180)
        if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 5000:
            return None, err or "转码失败"
        return dst, None

    def _silk_friendly(self, src, out_dir):
        """发语音前先自己降成 24k 单声道 + 留 2dB 真峰值余量。

        AstrBot 发 Record 时用 audioop.ratecv 粗暴重采样到 24000（无抗混叠），
        44.1k 立体声直接喂进去，镲片/鼓点的高频会折叠回来，听感就是"呲呲"爆破。
        这里先用 swr 高质量重采样并限一下峰值，silk 编码的压力小很多。
        """
        dst = os.path.join(out_dir, "voice24.mp3")
        rc, err = self._ff([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                            "-af", "alimiter=limit=0.79",
                            "-ac", "1", "-ar", "24000", "-b:a", "128k", dst],
                           timeout=180)
        if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 5000:
            logger.warning("[翻唱] 24k 预处理失败，按原样发: %s" % (err or "")[:100])
            return src
        return dst

    def _wrap_out(self, raw, label, mode=None):
        """决定发语音条还是文件。

        mode: "voice"=尽量发语音（群里能直接点开听，但要过 24k 单声道 silk）
              "file" =发文件（44.1k 立体声 mp3，音质完整，要下载）
              None   =按配置：超过 record_max_seconds / record_max_mb 才降级成文件
        注意：QQ 官方语音软限制 20MB，超过就必须走文件，发不出去的。
        """
        tmp = tempfile.mkdtemp(prefix="cover_out_")
        if raw[:4] == b"RIFF":
            ext = ".wav"
        elif raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3"):
            ext = ".mp3"
        else:
            ext = ".mp3"
        p = os.path.join(tmp, "cover" + ext)
        with open(p, "wb") as f:
            f.write(raw)
        dur = self._dur(p)
        over = dur > self.record_max_seconds or len(raw) > self.record_max_mb * 1048576
        too_big = len(raw) > 20 * 1048576      # QQ 语音软限制，超了只能发文件
        if mode == "file" or too_big or (mode is None and over):
            title = (label or "翻唱").strip("《》")
            return Comp.File(name="洛天依 - %s.mp3" % title, file=p)
        q = self._silk_friendly(p, tmp)
        with open(q, "rb") as f:
            return Comp.Record.fromBase64(base64.b64encode(f.read()).decode())

    # ---------------- 核心流程 ----------------
    def _cover_payload(self, b64, is_song):
        """组装发给本机的翻唱任务。

        选了预设（A/B）时，不再逐项传 index_rate/protect/... ——
        预设就是"整套参数"，逐项传会把预设顶掉（预设由本机 rvc_presets.json 统一管）。
        """
        p = {
            "audio_b64": b64,
            "pitch": self.pitch,
            "separate": bool(is_song),
            "out_tp": self.out_tp,
            "out_kbps": self.out_kbps,
            "harmony": self.harmony,
        }
        if self.preset:
            p["preset"] = self.preset
        else:
            p.update({
                "index_rate": self.index_rate,
                "protect": self.protect,
                "resample_sr": self.resample_sr,
                "f0_method": self.f0_method,
                "rms_mix_rate": self.rms_mix_rate,
            })
        return p

    async def _convert_and_send(self, event, src, label, dur_hint=0, mode=None):
        """已有一个可用的音频文件 → 派任务 → 按 mode 发语音或文件。"""
        mode = self._decide_mode(mode)
        tmpd = tempfile.mkdtemp(prefix="cover_")
        try:
            maxsec = self.song_seconds if label else self.max_seconds
            dur = dur_hint or self._dur(src)
            if dur and dur > maxsec:
                yield event.plain_result(
                    "这首 %d 秒，太长了，本小姐只唱前 %d 秒。" % (dur, maxsec))

            dst, err = await asyncio.get_event_loop().run_in_executor(
                None, self._prepare, src, tmpd, maxsec)
            if not dst:
                yield event.plain_result("音频处理失败：%s" % (err or "未知原因")[:120])
                return

            size_mb = os.path.getsize(dst) / 1048576.0
            logger.info("[翻唱] 送任务 %s / %.1f 秒 / %.1fMB" % (label or "音频", dur, size_mb))
            yield event.plain_result(
                "收到%s，本小姐去学学咋唱…（大概要一到两分钟）" % (label or "音频"))

            with open(dst, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, self._post, self._cover_payload(b64, bool(label)))
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:160]
                except Exception:
                    pass
                if e.code == 408:
                    yield event.plain_result("本机没接单（电脑关了 / 面板没开 / RVC 没起），这次先算了。")
                elif e.code == 413:
                    yield event.plain_result("音频太大了，换短一点的。")
                else:
                    yield event.plain_result("翻唱失败（HTTP %s）：%s" % (e.code, body))
                return
            except Exception as e:
                yield event.plain_result("翻唱失败：%s" % str(e)[:120])
                return

            out = (resp or {}).get("audio_b64") or ""
            if not out:
                yield event.plain_result("翻唱返回空音频。")
                return

            raw = base64.b64decode(out)
            logger.info("[翻唱] 完成，输出 %.0f KB / 发送方式=%s"
                        % (len(raw) / 1024, mode or "auto"))
            yield event.chain_result([self._wrap_out(raw, label, mode)])
        except Exception as e:
            logger.error("[翻唱] 异常: %s" % e)
            yield event.plain_result("翻唱出错了：%s" % str(e)[:120])
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

    async def _process(self, event, comp, mode=None):
        """处理用户直接发来的音频。"""
        tmpd = tempfile.mkdtemp(prefix="cover_src_")
        try:
            src, err = self._resolve_path(comp)
            if not src:
                yield event.plain_result(err)
                return
            async for r in self._convert_and_send(event, src, "", mode=mode):
                yield r
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

    async def _sing_song(self, event, song, mode=None):
        """下载并翻唱选中的那首。"""
        tmpd = tempfile.mkdtemp(prefix="cover_song_")
        try:
            yield event.plain_result("找到了：《%s》— %s，正在下…" % (song["name"], song["artists"]))
            path, err = await asyncio.get_event_loop().run_in_executor(
                None, self._download, song["id"], tmpd)

            # 拿不到（VIP 或网络失败）→ 先试 B 站
            if not path:
                logger.info("[翻唱] 网易云拿不到 %s（%s），转 B 站" % (song["name"], err))
                if self.enable_bili:
                    yield event.plain_result("网易云拿不到，去 B 站找找…")
                    bkw = ("%s %s" % (song["name"], song["artists"])).strip()
                    bpath, binfo = await self._bili_fetch(
                        bkw, song.get("duration", 0) / 1000.0, tmpd)
                    if bpath:
                        yield event.plain_result("B 站找到了：%s" % str(binfo)[:46])
                        async for r in self._convert_and_send(
                                event, bpath, "《%s》" % song["name"],
                                song.get("duration", 0) / 1000.0, mode=mode):
                            yield r
                        return
                    yield event.plain_result(
                        "B 站也不行（%s），换免费版本试试。" % str(binfo)[:60])

                # 再退到同名免费版本
                logger.info("[翻唱] %s 是 VIP 曲，找同名免费版本" % song["name"])
                alts = [s for s in (song.get("_alts") or []) if s.get("free")]
                for alt in alts[:3]:
                    path, err = await asyncio.get_event_loop().run_in_executor(
                        None, self._download, alt["id"], tmpd)
                    if path:
                        yield event.plain_result(
                            "换用免费版本：《%s》— %s" % (alt["name"], alt["artists"]))
                        break
                else:
                    yield event.plain_result(
                        "《%s》网易云和 B 站都拿不到音源。换一首，或者直接把音频发我。"
                        % song["name"])
                    return

            async for r in self._convert_and_send(event, path, "《%s》" % song["name"],
                                                  song.get("duration", 0) / 1000.0,
                                                  mode=mode):
                yield r
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

    # ---------------- 入口 ----------------
    @filter.command("点歌", alias={"唱", "翻唱歌", "sing"})
    async def song_cmd(self, event: AstrMessageEvent):
        """搜网上的歌，自动翻唱成洛天依的声音。用法：/点歌 起风了"""
        if not self.enabled or not self.enable_search:
            yield event.plain_result("点歌功能没开。")
            return

        raw_arg = self._arg(event, "点歌", "翻唱歌", "唱", "sing")
        kw, mode = self._parse_mode(raw_arg)
        if not kw:
            yield event.plain_result(
                "用法：/点歌 歌名\n比如 /点歌 起风了，我会搜出来让你选。\n"
                "想指定发送方式就在后面加词：/点歌 起风了 文件（或 语音）\n"
                "（也可以 /翻唱，然后直接把音频/语音发给我）")
            return

        try:
            songs = await asyncio.get_event_loop().run_in_executor(
                None, self._search, kw, 8)
        except Exception as e:
            logger.warning("[翻唱] 搜索失败: %s" % e)
            yield event.plain_result("搜索挂了：%s" % str(e)[:100])
            return

        if not songs:
            yield event.plain_result("没搜到《%s》，换个关键词？" % kw)
            return

        # 同名候选，给 VIP 兜底用
        for s in songs:
            s["_alts"] = songs

        umo = getattr(event, "unified_msg_origin", "") or ""
        uid = str(event.get_sender_id() or "")
        self._pending[umo] = {"songs": songs, "expire": time.time() + self.pick_ttl,
                              "uid": uid, "mode": mode}

        lines = ["找到 %d 首，回复序号（%d 秒内）：" % (len(songs), self.pick_ttl)]
        for i, s in enumerate(songs, 1):
            tag = "" if s["free"] else "  [VIP]"
            lines.append("%d. %s — %s%s" % (i, s["name"], s["artists"], tag))
        lines.append("回 0 取消。VIP 的会自动换免费版本。")
        if mode == "file":
            lines.append("这次发文件（音质完整）。")
        elif mode == "voice":
            lines.append("这次发语音条（直接听）。")
        yield event.plain_result("\n".join(lines))

    @filter.command("翻唱", alias={"cover"})
    async def cover_cmd(self, event: AstrMessageEvent):
        """把音频换成洛天依音色。用法：/翻唱（然后发音频），或 /翻唱 + 带音频"""
        if not self.enabled:
            yield event.plain_result("翻唱功能没开。")
            return

        _, mode = self._parse_mode(self._arg(event, "翻唱", "cover"))
        comp = self._find_audio(event)
        if comp is not None:
            if not self._mark(event):
                return
            async for r in self._process(event, comp, mode=mode):
                yield r
            return

        umo = getattr(event, "unified_msg_origin", "") or ""
        self._waiting[umo] = time.time() + self.wait_ttl
        if mode:
            self._wait_mode[umo] = mode
        yield event.plain_result(
            "行吧，把音频发给我——语音、音频文件都行，%d 秒以内。\n"
            "（%d 秒内不发就当你不唱了）%s"
            % (self.max_seconds, self.wait_ttl,
               "\n这次发%s。" % ("文件" if mode == "file" else "语音条") if mode else ""))

    @filter.command("发送方式", alias={"翻唱方式"})
    async def send_mode_cmd(self, event: AstrMessageEvent):
        """设置翻唱结果默认发语音还是文件。用法：/发送方式 语音|文件|自动"""
        t = (self._arg(event, "发送方式", "翻唱方式") or "").strip().lower()
        if "文件" in t or "file" in t or "音质" in t:
            self.send_mode = "file"
            yield event.plain_result(
                "好，以后翻唱发文件——完整音质，就是要下载。\n"
                "（某次想发语音：/点歌 歌名 语音）")
        elif "语音" in t or "voice" in t:
            self.send_mode = "voice"
            yield event.plain_result(
                "好，以后翻唱发语音条——群里直接点开听，但会被压成 24k 单声道。\n"
                "（某次想发文件：/点歌 歌名 文件）")
        else:
            self.send_mode = "auto"
            yield event.plain_result(
                "好，回到自动：%d 秒以内发语音，超了发文件。\n"
                "（这是临时设置，重启后回到面板里的 send_mode）"
                % self.record_max_seconds)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """等待音频状态下收音频；选歌状态下收序号。"""
        if not self.enabled:
            return
        umo = getattr(event, "unified_msg_origin", "") or ""
        now = time.time()

        # --- 选歌 ---
        pend = self._pending.get(umo)
        if pend:
            if now > pend["expire"]:
                self._pending.pop(umo, None)
            else:
                text = (event.message_str or "").strip()
                if text:
                    # 别的命令就不抢
                    if text.startswith(("/", "／", "#")):
                        pass
                    else:
                        m = re.match(r"^\s*[#／/]?\s*(\d{1,2})\s*$", text)
                        if m:
                            uid = str(event.get_sender_id() or "")
                            if pend.get("uid") and uid != pend["uid"]:
                                return
                            self._pending.pop(umo, None)
                            idx = int(m.group(1))
                            if idx == 0:
                                yield event.plain_result("行，那不唱了。")
                                return
                            songs = pend["songs"]
                            if not (1 <= idx <= len(songs)):
                                yield event.plain_result("没这个序号，重来一次 /点歌 吧。")
                                return
                            if not self._mark(event):
                                return
                            async for r in self._sing_song(event, songs[idx - 1],
                                                            mode=pend.get("mode")):
                                yield r
                            return

        # --- 等音频 ---
        dl = self._waiting.get(umo)
        if not dl:
            return
        if now > dl:
            self._waiting.pop(umo, None)
            return
        comp = self._find_audio(event)
        if comp is None:
            return
        self._waiting.pop(umo, None)
        wmode = self._wait_mode.pop(umo, None)
        if not self._mark(event):
            return
        async for r in self._process(event, comp, mode=wmode):
            yield r
