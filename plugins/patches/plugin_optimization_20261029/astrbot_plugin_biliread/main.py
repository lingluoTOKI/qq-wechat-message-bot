import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import aiohttp
from typing import Optional, Dict, Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from bilibili_api import video, Credential

from pydantic import Field
from pydantic.dataclasses import dataclass

# BVID 格式预编译正则：BV开头，后续为字母或数字
BVID_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10,12}")

# 消息里出现的 B 站链接（短链 / 标准链接 / BV 号）
LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:b23\.tv/[A-Za-z0-9]+|bilibili\.com/video/[A-Za-z0-9]+)"
    r"|BV[a-zA-Z0-9]{10,12}"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def inject_temp_content(req: ProviderRequest, text: str) -> None:
    """把插件附加内容作为临时片段送入本次请求。

    视频内容每次都不一样，写进 system_prompt 会让整段前缀缓存失效；
    改为追加到 extra_user_content_parts 并标记临时，不落历史、不动缓存。
    """
    try:
        from astrbot.core.agent.message import TextPart
        req.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())
    except Exception as e:
        logger.warning(f"BiliRead: 临时片段注入失败，回退 system_prompt: {e}")
        req.system_prompt += text


# 解析器下载视频时并发抓好的字幕/评论，存在这里供本插件直接复用
BRIDGE_DIR = Path(get_astrbot_plugin_data_path()) / "bili_bridge"
BRIDGE_TTL = 1800
# 解析器下载好的 mp4，抽帧/转写直接用它，不再二次下载
PARSER_CACHE_DIR = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_parser" / "cache"
VIDEO_CACHE_TTL = 1800
# 链接和追问分开发时，允许沿用最近一次链接的时长（秒）
RECENT_LINK_TTL = 600
# 同一支视频的读取结果复用时长，避免追问时重复调用模型
RESULT_TTL = 600
# 评论单页 20 条，多翻几页再按点赞排序，才拿得到热评
COMMENT_PAGES = 3
# 字幕为空/偏短时再取几次，保留最长的一份（AI 字幕分片下发）
SUBTITLE_ATTEMPTS = 3
# 字幕长到这个字数就认为拿全了，不再重复请求
SUBTITLE_ENOUGH = 1200
# parser 写的“会话最近链接”，重载/重启后仍能沿用
SESSION_LINKS_FILE = BRIDGE_DIR / "session_links.json"


def load_bridge_cache(bvid: str) -> Optional[Dict[str, Any]]:
    """读取解析器旁路抓取的字幕/评论缓存；过期或不存在返回 None。"""
    path = BRIDGE_DIR / f"{bvid}-0.json"
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > BRIDGE_TTL:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"BiliRead: 读取共享缓存失败: {e}")
        return None


def find_downloaded_video(bvid: str) -> Optional[str]:
    """找解析器已下载好的视频文件，用于抽帧和转写。"""
    try:
        if not PARSER_CACHE_DIR.exists():
            return None
        for path in PARSER_CACHE_DIR.glob(f"*{bvid}*.mp4"):
            if time.time() - path.stat().st_mtime <= VIDEO_CACHE_TTL:
                return str(path)
    except Exception as e:
        logger.warning(f"BiliRead: 查找已下载视频失败: {e}")
    return None


def load_session_link(umo: str) -> Optional[Dict[str, Any]]:
    """读取本会话最近的 B 站链接（parser 在收到链接时就写好）。"""
    try:
        if not SESSION_LINKS_FILE.exists():
            return None
        data = json.loads(SESSION_LINKS_FILE.read_text(encoding="utf-8"))
        entry = data.get(umo) if isinstance(data, dict) else None
        if not isinstance(entry, dict):
            return None
        if time.time() - float(entry.get("ts") or 0) > RECENT_LINK_TTL:
            return None
        if not (entry.get("bvid") or entry.get("link")):
            return None
        return entry
    except Exception as e:
        logger.warning(f"BiliRead: 读取会话链接失败: {e}")
        return None


def save_session_link(umo: str, link: str, bvid: str = "") -> None:
    """把最近链接写回共享文件；解析出 BV 后补上，避免追问时再跳短链。"""
    if not umo or not link:
        return
    now = time.time()
    try:
        data: Dict[str, Any] = {}
        if SESSION_LINKS_FILE.exists():
            raw = json.loads(SESSION_LINKS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        old = data.get(umo) or {}
        data[umo] = {
            "link": link,
            "bvid": bvid or str(old.get("bvid") or ""),
            "ts": now,
        }
        for key, value in list(data.items()):
            if now - float((value or {}).get("ts") or 0) > BRIDGE_TTL:
                data.pop(key, None)
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SESSION_LINKS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SESSION_LINKS_FILE)
    except Exception as e:
        logger.warning(f"BiliRead: 写会话链接失败: {e}")


async def _fetch_subtitle_once(bvid: str, cid: int, sessdata: str = "") -> str:
    """取一次中文字幕正文；无字幕或失败返回空串。

    不用 bilibili_api 的 get_subtitle：它走 x/player/wbi/v2，机房 IP 常被 412 风控，
    改用同样返回字幕列表的 x/player/v2。
    """
    if not bvid or not cid:
        return ""
    headers = {
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }
    if sessdata:
        headers["Cookie"] = "SESSDATA=" + sessdata
    list_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={int(cid)}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(list_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"BiliRead: 字幕列表 HTTP {resp.status}")
                    return ""
                subtitle_info = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"BiliRead: 读取字幕列表失败: {e}")
        return ""
    if (subtitle_info or {}).get("code") != 0:
        logger.warning(f"BiliRead: 字幕列表接口返回 {subtitle_info.get('code')}")
        return ""
    subs = (((subtitle_info.get("data") or {}).get("subtitle") or {}).get("subtitles")) or []
    target = None
    for sub in subs:
        if str(sub.get("lan", "")).startswith("zh"):
            target = sub
            break
    if target is None and subs:
        target = subs[0]
    subtitle_url = (target or {}).get("subtitle_url", "")
    if not subtitle_url:
        return ""
    if not subtitle_url.startswith("http"):
        subtitle_url = "https:" + subtitle_url
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(
                subtitle_url,
                headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"BiliRead: 字幕下载 HTTP {resp.status}")
                    return ""
                subtitle_json = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"BiliRead: 字幕下载异常: {e}")
        return ""
    body = (subtitle_json or {}).get("body", [])
    return "\n".join(item.get("content", "") for item in body).strip()


async def fetch_subtitle(bvid: str, cid: int, sessdata: str = "") -> str:
    """取中文字幕正文，多取几次保留最长的一份。

    B 站 AI 字幕是分片下发的，单次请求可能只给一小段（甚至空），
    多请求几次取最长的那份；已经够长就不再等。
    """
    best = ""
    for attempt in range(1, SUBTITLE_ATTEMPTS + 1):
        text = await _fetch_subtitle_once(bvid, cid, sessdata)
        if len(text) > len(best):
            best = text
        if len(best) >= SUBTITLE_ENOUGH:
            break
        if attempt < SUBTITLE_ATTEMPTS:
            await asyncio.sleep(1.2)
    return best


async def fetch_comment_page(aid: int, sessdata: str, page: int, count: int = 20) -> list:
    """读取评论区某一页。失败返回空列表。"""
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if sessdata:
        headers["Cookie"] = "SESSDATA=" + sessdata
    url = (
        f"https://api.bilibili.com/x/v2/reply?type=1&oid={int(aid)}"
        f"&pn={int(page)}&ps={int(count)}&sort=2"
    )
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"BiliRead: 读取评论区失败(第 {page} 页): {e}")
        return []
    if (data or {}).get("code") != 0:
        logger.warning(f"BiliRead: 评论区接口返回 {data.get('code')} (第 {page} 页)")
        return []
    return ((data.get("data") or {}).get("replies")) or []


async def fetch_comments(aid: int, sessdata: str, count: int = 20) -> str:
    """读取热评（公开接口，0 token）。无评论或失败时返回空串。

    接口的 sort 参数并不真按点赞排序，所以多翻几页再按点赞数本地排序，
    取最高的 count 条才是热评。
    """
    if not aid or count <= 0:
        return ""
    pages = await asyncio.gather(
        *(fetch_comment_page(aid, sessdata, p, count) for p in range(1, COMMENT_PAGES + 1)),
        return_exceptions=True,
    )
    replies: list = []
    for item in pages:
        if isinstance(item, BaseException):
            continue
        replies.extend(item)
    if not replies:
        return ""

    seen = set()
    unique = []
    for item in sorted(replies, key=lambda r: int(r.get("like") or 0), reverse=True):
        rpid = item.get("rpid")
        if rpid in seen:
            continue
        seen.add(rpid)
        unique.append(item)

    lines = []
    for item in unique[:count]:
        msg = ((item.get("content") or {}).get("message") or "").replace("\n", " ").strip()
        if not msg:
            continue
        name = ((item.get("member") or {}).get("uname") or "匿名")[:12]
        lines.append(f"{name}：{msg[:120]}")
    return "\n".join(lines)


async def download_audio(url: str, max_seconds: int) -> Optional[str]:
    """下载音频并转成 16k 单声道 wav，供语音识别使用。"""
    workdir = tempfile.mkdtemp(prefix="biliread_")
    raw = os.path.join(workdir, "audio.m4s")
    wav = os.path.join(workdir, "audio.wav")
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"BiliRead: 音频下载失败 HTTP {resp.status}")
                    return None
                with open(raw, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", "-i", raw,
            "-t", str(int(max_seconds)), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", wav,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await proc.wait() != 0 or not os.path.exists(wav):
            logger.warning("BiliRead: ffmpeg 转换音频失败")
            return None
        os.remove(raw)
        return wav
    except Exception as e:
        logger.warning(f"BiliRead: 音频处理异常: {e}")
        return None


async def extract_local_audio(video_path: str, max_seconds: int) -> Optional[str]:
    """从解析器已下载的 mp4 里抽音频，避免为转写再下一次。"""
    workdir = tempfile.mkdtemp(prefix="biliread_")
    wav = os.path.join(workdir, "audio.wav")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", "-i", video_path,
            "-t", str(int(max_seconds)), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", wav,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("BiliRead: 本地音频转换超时")
            shutil.rmtree(workdir, ignore_errors=True)
            return None
        if code != 0 or not os.path.exists(wav):
            logger.warning("BiliRead: 本地音频转换失败")
            shutil.rmtree(workdir, ignore_errors=True)
            return None
        return wav
    except Exception as e:
        logger.warning(f"BiliRead: 本地音频处理异常: {e}")
        shutil.rmtree(workdir, ignore_errors=True)
        return None


def get_stt_provider(context, provider_id: str):
    """取语音识别 provider；未指定时退回当前默认 STT。"""
    manager = getattr(context, "provider_manager", None)
    inst_map = getattr(manager, "inst_map", {}) or {}
    inst = inst_map.get(provider_id) if provider_id else None
    if inst is None:
        inst = getattr(manager, "curr_stt_provider_inst", None)
    return inst


async def transcribe_audio(context, provider_id: str, audio_url: str) -> str:
    stt = get_stt_provider(context, provider_id)
    if stt is None or not hasattr(stt, "get_text"):
        logger.warning(f"BiliRead: 未找到语音识别 provider（{provider_id}）")
        return ""
    try:
        text = await stt.get_text(audio_url)
    except Exception as e:
        logger.warning(f"BiliRead: 音频转写失败: {e}")
        return ""
    return (text or "").strip()



VISION_PROMPT = (
    "这些是同一个B站视频的若干关键帧截图。请只依据画面回答：\n"
    "1. 画面上出现的文字，逐条转写（标题、歌词、字幕、弹幕、封面字样），"
    "看不清就标“不确定”，不要猜测；\n"
    "2. 主要人物、场景、画面内容；\n"
    "3. 判断视频类型（音乐MV/翻唱/演奏/游戏实况/其他）。\n"
    "如果画面是纯文字封面或歌曲信息页，请优先提取歌名、歌手、作者等字样。"
)


async def download_video_segment(url: str, max_seconds: int) -> Optional[tuple]:
    """下载视频前若干秒（流拷贝，不重编码），返回 (片段路径, 临时目录)。"""
    workdir = tempfile.mkdtemp(prefix="bilivision_")
    clip = os.path.join(workdir, "clip.mp4")
    headers = (
        "Referer: https://www.bilibili.com/\r\n"
        f"User-Agent: {UA}\r\n"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error",
            "-headers", headers,
            "-i", url,
            "-t", str(int(max_seconds)), "-c", "copy",
            clip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("BiliRead: 下载视频片段超时")
            shutil.rmtree(workdir, ignore_errors=True)
            return None
        if code != 0 or not os.path.exists(clip):
            logger.warning("BiliRead: ffmpeg 下载视频片段失败")
            shutil.rmtree(workdir, ignore_errors=True)
            return None
        return clip, workdir
    except Exception as e:
        logger.warning(f"BiliRead: 下载视频片段异常: {e}")
        shutil.rmtree(workdir, ignore_errors=True)
        return None


async def extract_frames(
    clip_path: str, workdir: str, count: int, clip_seconds: float = 60.0
) -> list:
    """从本地视频片段均匀抽帧，返回 jpg 路径列表。"""
    frames = []
    positions = [0.12, 0.45, 0.78, 0.95][: max(1, int(count))]
    for idx, pos in enumerate(positions):
        out = os.path.join(workdir, f"frame_{idx}.jpg")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{max(1.0, pos * clip_seconds):.1f}",
            "-i", clip_path,
            "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3",
            out,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("BiliRead: 抽帧超时")
            continue
        if code == 0 and os.path.exists(out):
            frames.append(out)
    return frames


async def describe_frames(
    api_key: str, api_base: str, model: str, frames: list, timeout: int = 30
) -> str:
    """调用视觉模型读取关键帧，返回画面描述。"""
    if not api_key or not frames:
        return ""
    content = [{"type": "text", "text": VISION_PROMPT}]
    for path in frames:
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except OSError as e:
            logger.warning(f"BiliRead: 读取关键帧失败: {e}")
            continue
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )
    if len(content) == 1:
        return ""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 600,
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    url = api_base.rstrip("/") + "/chat/completions"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"BiliRead: 视觉模型调用异常: {e}")
        return ""
    if not isinstance(data, dict) or data.get("error"):
        logger.warning(f"BiliRead: 视觉模型返回异常: {str(data)[:200]}")
        return ""
    choices = data.get("choices") or []
    if not choices:
        logger.warning("BiliRead: 视觉模型无返回内容")
        return ""
    content = ((choices[0] or {}).get("message") or {}).get("content", "")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content).strip()


async def resolve_b23(short_url: str) -> str:
    """
    解析 b23.tv 短链，返回真实的长链接
    """
    timeout = aiohttp.ClientTimeout(total=10)

    if not short_url.startswith("http"):
        short_url = "https://" + short_url

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 第一次请求
        async with session.get(short_url, allow_redirects=False) as response:
            real_url = response.headers.get("Location", short_url)

        # 处理重定向
        max_redirects = 10
        for _ in range(max_redirects):
            if not real_url.startswith("http"):
                break
            async with session.get(real_url, allow_redirects=False) as response:
                next_url = response.headers.get("Location")
                if not next_url:
                    break
                real_url = next_url

    # 提取BVID
    match = BVID_PATTERN.search(real_url)

    logger.info(f"解析b23.tv短链成功：{short_url} -> {real_url}")

    bvid = match.group(0) if match else ""
    if not bvid:
        logger.error(f"解析b23.tv短链失败：{short_url} -> {real_url}")
        return "error"

    logger.info(f"解析视频链接成功：{short_url} -> {bvid}")

    return bvid


@dataclass(config=dict(arbitrary_types_allowed=True))
class BilibiliTool(FunctionTool[AstrAgentContext]):
    name: str = "bilibili_read"
    description: str = (
        "读取哔哩哔哩视频的内容：字幕、热门评论、音频转写、关键帧画面识别。"
        "当用户发来 BV 号或 b23.tv/bilibili.com 链接，或问“能看/能听/讲了什么/评价一下”时调用，"
        "歌曲和音乐 MV 同样适用。不要只凭标题猜测内容。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "bvid": {
                    "type": "string",
                    "description": "想要获取的哔哩哔哩视频的BVID或是b23.tv链接，例如BV1GJ411x7h7或https://b23.tv/4bdIZBf",
                },
            },
            "required": ["bvid"],
        }
    )

    # 配置参数
    sessdata: str = ""
    bili_jct: str = ""
    ct: Context = Field(default=None)
    llm_provider_id: str = ""
    # 新增：字幕最大长度限制，防止上下文溢出
    max_subtitle_length: int = 4000
    # 兜底：评论区 + 音频转写
    enable_comments: bool = True
    comment_count: int = 20
    enable_asr: bool = True
    asr_provider_id: str = ""
    asr_max_seconds: int = 180
    asr_min_comment_chars: int = 200
    max_desc_length: int = 200
    # 视觉兜底：无字幕时抽帧交给视觉模型读画面
    enable_vision: bool = True
    vision_api_key: str = ""
    vision_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    vision_model: str = "glm-4v-flash"
    vision_max_frames: int = 3
    vision_max_seconds: int = 60
    vision_timeout: int = 30
    # 同一支视频短期内重复读取（链接一条、追问一条）直接复用结果
    result_cache: dict = Field(default_factory=dict)

    def _check_config(self) -> Optional[str]:
        """防御性检查：确保核心依赖已注入"""
        if not self.ct:
            return "插件内部错误：上下文未注入"
        if not self.llm_provider_id:
            return "插件配置错误：未配置 llm_provider_id"
        return None

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        # 1. 防御性检查
        config_err = self._check_config()
        if config_err:
            return config_err

        bvid = kwargs.get("bvid", "").strip()

        # 2. 格式校验
        if "b23" in bvid:
            bvid = await resolve_b23(bvid)
        elif not BVID_PATTERN.match(bvid):
            bvid = await resolve_b23("https://b23.tv/" + bvid)

        if bvid == "error":
            return "解析b23.tv短链失败，请检查链接是否正确"

        # 自动注入路径要原文：直接返回字幕/评论，不再套一层 LLM 总结
        want_raw = bool(kwargs.get("raw"))
        cache_key = ("raw:" + bvid) if want_raw else bvid
        cached_result = self.result_cache.get(cache_key)
        if cached_result and time.time() - cached_result[1] <= RESULT_TTL:
            logger.info(f"BiliRead: 复用 {bvid} 的读取结果")
            return cached_result[0]

        logger.info(f"开始解析视频：{bvid}")

        # 3. 初始化凭证
        credential = Credential(sessdata=self.sessdata, bili_jct=self.bili_jct)
        v = video.Video(bvid, credential=credential)

        try:
            # 4. 优先复用解析器下载视频时抓好的字幕/评论
            cache = load_bridge_cache(bvid)
            if cache:
                title = str(cache.get("title") or "未知标题")
                owner = str(cache.get("owner") or "")[:20]
                desc = re.sub(r"\s+", " ", str(cache.get("desc") or "")).strip()
                desc = desc[: self.max_desc_length]
                duration = int(cache.get("duration") or 0)
                cid = int(cache.get("cid") or 0)
                aid = int(cache.get("aid") or 0)
                if not cid:
                    try:
                        cid = await v.get_cid(0)
                    except Exception:
                        cid = 0
                logger.info(f"BiliRead: 复用解析器抓好的字幕/评论 {bvid}")
            else:
                # 4.1 没有缓存时自己取基础信息
                info = await v.get_info()
                title = info.get("title", "未知标题")
                cid = await v.get_cid(0)
                aid = int(info.get("aid") or 0)
                duration = int(info.get("duration") or 0)
                owner = str(((info.get("owner") or {}).get("name")) or "")[:20]
                desc = re.sub(r"\s+", " ", str(info.get("desc") or "")).strip()
                desc = desc[: self.max_desc_length]

            # 5. 字幕和评论都要
            subtitle_text = str((cache or {}).get("subtitle") or "")
            if not subtitle_text:
                # 解析器可能没登录，用本插件凭证再试一次
                subtitle_text = await fetch_subtitle(bvid, cid, self.sessdata)
                if subtitle_text:
                    logger.info(f"BiliRead: 获取字幕 {len(subtitle_text)} 字")

            comments_text = str((cache or {}).get("comments") or "")
            if self.enable_comments:
                # 解析器没登录时接口只给 3 条热门评论；本插件有登录态就重新抓满
                if self.sessdata or not comments_text:
                    fresh = await fetch_comments(
                        aid, self.sessdata, self.comment_count
                    )
                    if fresh and len(fresh) > len(comments_text):
                        comments_text = fresh
                        logger.info(f"BiliRead: 读取评论 {len(fresh)} 字")
                    elif not comments_text:
                        comments_text = fresh

            labels = []
            if subtitle_text:
                labels.append("字幕")
            if comments_text:
                labels.append("热门评论")
            content_label = " + ".join(labels)

            # 字幕再长也要给评论留位置，否则截断会把评论整段吃掉
            if subtitle_text and comments_text:
                budget = self.max_subtitle_length - len(comments_text) - 12
                budget = max(budget, min(self.max_subtitle_length // 2, 800))
                if len(subtitle_text) > budget:
                    subtitle_text = subtitle_text[:budget] + "\n...(字幕后续已省略)"
            content_text = ""
            if subtitle_text:
                content_text = subtitle_text
            if comments_text:
                content_text = (content_text + "\n\n") if content_text else ""
                content_text += "【热门评论】\n" + comments_text
            if content_text and len(content_text) > self.max_subtitle_length:
                logger.info(f"内容过长 ({len(content_text)} 字符)，已截断")
                content_text = (
                    content_text[: self.max_subtitle_length]
                    + "\n...(后续内容已省略)"
                )

            # 6. 无字幕且评论不足时再对音频做转写
            need_asr = self.enable_asr and not subtitle_text and (
                len(comments_text) < self.asr_min_comment_chars
            )
            if need_asr:
                wav_path = None
                # 解析器已下载的 mp4 里直接抽音频，不再二次下载
                local_video = find_downloaded_video(bvid)
                if local_video:
                    wav_path = await extract_local_audio(
                        local_video, self.asr_max_seconds
                    )
                if not wav_path:
                    audio_url = ""
                    try:
                        download_info = await v.get_download_url(cid=cid)
                        audios = ((download_info or {}).get("dash") or {}).get("audio") or []
                        if audios:
                            audio_url = (
                                min(audios, key=lambda a: int(a.get("bandwidth") or 0)).get(
                                    "baseUrl"
                                )
                                or ""
                            )
                    except Exception as e:
                        logger.warning(f"BiliRead: 获取音频地址失败: {e}")
                    if audio_url:
                        wav_path = await download_audio(
                            audio_url, self.asr_max_seconds
                        )
                if wav_path:
                    try:
                        speech = await transcribe_audio(
                            self.ct, self.asr_provider_id, wav_path
                        )
                    finally:
                        try:
                            os.remove(wav_path)
                        except OSError:
                            pass
                    if speech:
                        logger.info(
                            f"BiliRead: 音频转写 {len(speech)} 字"
                            f"（前 {self.asr_max_seconds}s）"
                        )
                        content_text = (
                            (content_text + "\n\n") if content_text else ""
                        ) + speech
                        content_label = (
                            f"{content_label} + 音频转写"
                            if content_label
                            else "音频转写"
                        )
                        if len(content_text) > self.max_subtitle_length:
                            content_text = (
                                content_text[: self.max_subtitle_length]
                                + "\n...(后续内容已省略)"
                            )
                else:
                    logger.info("BiliRead: 未取到音频，跳过转写")

            # 7. 仍无字幕时，抽帧交给视觉模型读画面文字（音乐区常用）
            if (
                self.enable_vision
                and self.vision_api_key
                and not subtitle_text
            ):
                clip_path = ""
                clip_workdir = ""
                try:
                    clip_seconds = self.vision_max_seconds
                    if duration:
                        clip_seconds = min(duration, self.vision_max_seconds)
                    # 解析器已下载的 mp4 直接抽帧，不再二次下载
                    local_video = find_downloaded_video(bvid)
                    if local_video:
                        clip_path = local_video
                        clip_workdir = tempfile.mkdtemp(prefix="bilivision_")
                    else:
                        video_url = ""
                        download_info = await v.get_download_url(cid=cid)
                        videos = ((download_info or {}).get("dash") or {}).get("video") or []
                        if videos:
                            ordered = sorted(
                                videos,
                                key=lambda a: (
                                    int(a.get("height") or 0),
                                    int(a.get("bandwidth") or 0),
                                ),
                            )
                            readable = [
                                a
                                for a in ordered
                                if int(a.get("height") or 0) >= 480
                            ]
                            picked = readable[0] if readable else ordered[0]
                            video_url = picked.get("baseUrl") or picked.get("base_url") or ""
                        if video_url:
                            got = await download_video_segment(video_url, clip_seconds)
                            if got:
                                clip_path, clip_workdir = got
                        else:
                            logger.info("BiliRead: 未取到视频地址，跳过抽帧")
                    if clip_path:
                        frames = await extract_frames(
                            clip_path, clip_workdir, self.vision_max_frames,
                            float(clip_seconds),
                        )
                        logger.info(
                            f"BiliRead: 抽帧 {len(frames)} 张"
                            f"（前 {clip_seconds}s）"
                        )
                        vision_text = await describe_frames(
                            self.vision_api_key, self.vision_api_base,
                            self.vision_model, frames, self.vision_timeout,
                        )
                        if vision_text:
                            logger.info(
                                f"BiliRead: 画面识别 {len(vision_text)} 字"
                            )
                            content_text = (
                                (content_text + "\n\n") if content_text else ""
                            ) + vision_text
                            content_label = (
                                f"{content_label} + 画面识别"
                                if content_label
                                else "画面识别"
                            )
                            if len(content_text) > self.max_subtitle_length:
                                content_text = (
                                    content_text[: self.max_subtitle_length]
                                    + "\n...(后续内容已省略)"
                                )
                except Exception as e:
                    logger.warning(f"BiliRead: 视觉兜底失败: {e}")
                finally:
                    if clip_workdir:
                        shutil.rmtree(clip_workdir, ignore_errors=True)

            # 自动注入：把抓到的字幕/评论原文直接交给主模型
            if want_raw:
                lines = [f"视频标题：《{title}》"]
                if owner:
                    lines.append(f"UP主：{owner}")
                if duration:
                    lines.append(f"时长：{duration // 60} 分 {duration % 60} 秒")
                if desc:
                    lines.append(f"简介：{desc}")
                if content_label:
                    lines.append(f"信息来源：{content_label}")
                lines.append("")
                lines.append(
                    content_text
                    if content_text
                    else "（没有字幕、评论或音频转写，只有标题和简介）"
                )
                raw_text = "\n".join(lines)
                if self.result_cache is None:
                    self.result_cache = {}
                if len(self.result_cache) > 64:
                    self.result_cache.clear()
                self.result_cache[cache_key] = (raw_text, time.time())
                return raw_text

            # 10. 调用 LLM
            meta = [f"视频标题：《{title}》"]
            if owner:
                meta.append(f"UP主：{owner}")
            if duration:
                meta.append(f"时长：{duration // 60} 分 {duration % 60} 秒")
            if desc:
                meta.append(f"简介：{desc}")
            if content_text:
                body_text = f"信息来源：{content_label}\n{content_text}"
                ask = (
                    "请根据上述信息总结视频的核心观点，保留关键信息；"
                    "不要编造信息里没有的情节。"
                )
            else:
                body_text = "没有字幕、评论或音频转写，只能依据标题与简介。"
                ask = (
                    "请只依据标题与简介做简短推测，并说明信息有限；"
                    "不要编造具体内容。"
                )
            prompt = "\n".join(meta) + "\n\n" + body_text + "\n\n" + ask

            ai_resp = await self.ct.llm_generate(
                chat_provider_id=self.llm_provider_id,
                prompt=prompt,
            )
            if self.result_cache is None:
                self.result_cache = {}
            if len(self.result_cache) > 64:
                self.result_cache.clear()
            self.result_cache[bvid] = (ai_resp, time.time())
            return ai_resp

        except aiohttp.ClientError as e:
            logger.error(f"网络请求异常: {e}")
            return "网络请求异常，请稍后重试。"
        except KeyError as e:
            logger.error(f"数据解析异常，结构可能发生变更: {e}")
            return "解析字幕数据时发生错误，可能是 API 结构变更。"
        except Exception as e:
            # 捕获 bilibili_api 抛出的其他异常或未知异常
            # 建议在日志中打印完整堆栈
            logger.exception(f"处理 BVID {bvid} 时发生未知错误")
            return f"处理视频时发生内部错误: {str(e)}"


@register(
    "astrbot_plugin_biliread",
    "SodaCode & CodeBuddy & GLM-5",
    "告别机械式的视频大纲复制，让你的 AstrBot 拥有真正“看懂” B 站视频的能力。",
    "1.1.2",
)
class BiliRead(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        # 1. 安全的配置读取
        # 兼容 config 是字典或 Pydantic 对象的情况
        if isinstance(config, dict):
            plugin_config = config
        elif hasattr(config, "model_dump"):
            # Pydantic v2
            plugin_config = config.model_dump()
        elif hasattr(config, "dict"):
            # Pydantic v1
            plugin_config = config.dict()
        else:
            logger.warning(f"不支持的配置类型: {type(config)}，使用默认空配置。")
            plugin_config = {}

        # 2. 提取配置项
        bilibili_cookie = plugin_config.get("bilibili_cookie", {})

        # 明确语义：不再使用有歧义的 'id'，统一读取 'bili_jct'
        # 如果用户配置了 'id'，代码逻辑上也可以尝试兼容读取，但优先使用正确键名
        sessdata = bilibili_cookie.get("sessdata", "")
        bili_jct = bilibili_cookie.get("bili_jct", bilibili_cookie.get("id", ""))
        llm_provider_id = plugin_config.get("llm_provider_id", "")
        max_len = plugin_config.get("max_subtitle_length", 4000)
        enable_comments = plugin_config.get("enable_comments", True)
        comment_count = plugin_config.get("comment_count", 20)
        enable_asr = plugin_config.get("enable_asr", True)
        asr_provider_id = plugin_config.get("asr_provider_id", "") or ""
        asr_max_seconds = plugin_config.get("asr_max_seconds", 180)
        asr_min_comment_chars = plugin_config.get("asr_min_comment_chars", 200)
        max_desc_length = plugin_config.get("max_desc_length", 200)
        enable_vision = plugin_config.get("enable_vision", True)
        vision_api_key = plugin_config.get("vision_api_key", "") or ""
        vision_api_base = (
            plugin_config.get("vision_api_base", "")
            or "https://open.bigmodel.cn/api/paas/v4"
        )
        vision_model = plugin_config.get("vision_model", "") or "glm-4v-flash"
        vision_max_frames = plugin_config.get("vision_max_frames", 3)
        vision_max_seconds = plugin_config.get("vision_max_seconds", 60)
        vision_timeout = plugin_config.get("vision_timeout", 30)

        # 3. 配置完整性校验日志
        if not sessdata:
            logger.warning(
                "BiliRead: SESSDATA 未配置，可能导致无法获取高质量字幕或鉴权失败。"
            )
        if not bili_jct:
            logger.warning("BiliRead: bili_jct 未配置。")
        if not llm_provider_id:
            logger.error("BiliRead: llm_provider_id 未配置，LLM 功能将不可用。")
        if enable_vision and not vision_api_key:
            logger.warning("BiliRead: 视觉兜底已启用但未配置 vision_api_key。")

        # 4. 注册工具
        tool = BilibiliTool(
            sessdata=sessdata,
            bili_jct=bili_jct,
            ct=self.context,
            llm_provider_id=llm_provider_id,
            max_subtitle_length=max_len,
            enable_comments=bool(enable_comments),
            comment_count=int(comment_count or 20),
            enable_asr=bool(enable_asr),
            asr_provider_id=str(asr_provider_id),
            asr_max_seconds=int(asr_max_seconds or 180),
            asr_min_comment_chars=int(asr_min_comment_chars or 200),
            max_desc_length=int(max_desc_length or 200),
            enable_vision=bool(enable_vision),
            vision_api_key=str(vision_api_key),
            vision_api_base=str(vision_api_base),
            vision_model=str(vision_model),
            vision_max_frames=int(vision_max_frames or 3),
            vision_max_seconds=int(vision_max_seconds or 60),
            vision_timeout=int(vision_timeout or 30),
        )
        self.context.add_llm_tools(tool)
        self.tool = tool
        # 会话最近一条 B 站链接，供“先发链接、再单独提问”的场景沿用
        self._recent_links: dict[str, tuple[str, float]] = {}

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def remember_bilibili_link(self, event: AstrMessageEvent):
        """只记录本会话最近出现的 B 站链接，不拦消息、不触发回复。"""
        text = event.message_str or ""
        found = LINK_PATTERN.search(text)
        if not found:
            return
        umo = event.unified_msg_origin
        link = found.group(0)
        now = time.time()
        self._recent_links[umo] = (link, now)
        save_session_link(umo, link)
        logger.info(f"BiliRead: 记录会话链接 {umo} -> {link}")
        # 短链在后台解析成 BV，追问时就不用再跳一次
        if "b23" in link:
            asyncio.create_task(self._resolve_recent_bvid(umo, link))
        if len(self._recent_links) > 64:
            expired = [
                key
                for key, value in self._recent_links.items()
                if now - value[1] > RECENT_LINK_TTL
            ]
            for key in expired:
                self._recent_links.pop(key, None)

    async def _resolve_recent_bvid(self, umo: str, link: str) -> None:
        """把 b23.tv 短链解析成 BV，回填到内存和共享文件。"""
        try:
            bvid = await resolve_b23(link)
        except Exception as e:
            logger.warning(f"BiliRead: 短链解析失败: {e}")
            return
        if not bvid or bvid == "error":
            return
        current = self._recent_links.get(umo)
        if current and current[0] == link:
            self._recent_links[umo] = (bvid, time.time())
        save_session_link(umo, link, bvid)
        logger.info(f"BiliRead: 短链已解析 {link} -> {bvid}")

    def _recent_link(self, umo: str) -> tuple[str, str]:
        """按“内存 → 共享文件”的顺序找本会话最近的链接，返回 (链接, 来源)。"""
        recent = self._recent_links.get(umo)
        if recent and time.time() - recent[1] <= RECENT_LINK_TTL:
            return recent[0], "内存"
        entry = load_session_link(umo)
        if entry:
            target = str(entry.get("bvid") or entry.get("link") or "")
            if target:
                return target, "共享缓存"
        return "", ""

    @filter.on_llm_request()
    async def inject_bilibili_content(self, event: AstrMessageEvent, req: ProviderRequest):
        """消息里带 B 站链接时自动读取内容并注入上下文。

        不完全依赖模型主动调用 bilibili_read：模型常常改去搜索或直接猜标题。
        当前消息没链接时，沿用本会话最近 10 分钟内的链接，兼容“链接、提问分两条发”。
        """
        if event.get_extra("biliread_autoread"):
            return
        umo = event.unified_msg_origin
        text = event.message_str or ""
        found = LINK_PATTERN.search(text)
        if found:
            link = found.group(0)
            source = "当前消息"
        else:
            link, source = self._recent_link(umo)
            if not link:
                return
            logger.info(f"BiliRead: 当前消息无链接，沿用最近链接（{source}）：{link}")
        event.set_extra("biliread_autoread", True)
        # 标准链接只取 BV 号；短链原样交给工具解析
        target = link
        if "b23" not in link:
            bv = BVID_PATTERN.search(link)
            if bv:
                target = bv.group(0)
        logger.info(f"BiliRead: 自动读取 {target}（来源：{source}）")
        try:
            content = await self.tool.call(None, bvid=target, raw=True)
        except Exception as e:
            logger.warning(f"BiliRead: 自动读取失败: {e}")
            inject_temp_content(req, (
                "\n\n【B站视频自动读取失败】本次没有取到视频内容，"
                "请直接说明暂时读不到，不要拿之前聊过的其他视频或标题猜内容。"
            ))
            return
        if not content or not str(content).strip():
            logger.warning(f"BiliRead: {target} 未读取到有效内容")
            inject_temp_content(req, (
                "\n\n【B站视频自动读取为空】本次没有取到视频内容，"
                "请直接说明暂时读不到，不要拿之前聊过的其他视频或标题猜内容。"
            ))
            return
        # 把 BV 和标题一起标注，避免模型把这次的内容和上一支视频混在一起
        header = f"【B站视频 {target}】"
        bv = BVID_PATTERN.search(target or "")
        bridge = load_bridge_cache(bv.group(0)) if bv else None
        title = str((bridge or {}).get("title") or "")
        if title:
            header = f"【B站视频 {bv.group(0)}：《{title}》】"
        inject_temp_content(req, (
            f"\n\n{header}（插件已直接提取字幕/评论原文，可直接引用）\n"
            f"{content}\n"
            "用户问的就是这支视频，请基于上面的实际内容回答；"
            "不要说看不到/听不到，也不要把别的视频当成本次内容。"
        ))
        logger.info("BiliRead: 已把视频内容注入当前 LLM 请求")

    async def initialize(self):
        pass

    # @filter.command("biliread")
    # async def biliread(self, event: AstrMessageEvent):
    #     yield event.plain_result(
    #         "BiliRead 插件已就绪。请直接发送 BVID 或让 AI 调用工具。"
    #     )

    async def terminate(self):
        pass
