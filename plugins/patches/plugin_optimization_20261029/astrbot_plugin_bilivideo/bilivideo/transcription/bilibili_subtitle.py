"""Native Bilibili subtitle fetcher.

yt-dlp 抓 B 站字幕会撞 412(WBI 风控); 老接口 `x/player/v2` 不签名、带 Cookie
就能拿到 `ai-zh` 轨道, 所以优先走这里, 失败再交给 yt-dlp。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

from ..core.exceptions import BiliVideoError
from ..core.logging import get_logger
from ..core.types import TranscriptResult, TranscriptSegment

logger = get_logger("BiliVideo/Subtitle")

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_PLAYER_V2 = "https://api.bilibili.com/x/player/v2"
_VIEW = "https://api.bilibili.com/x/web-interface/view"


def _extract_bvid(video_url: str) -> str | None:
    m = _BV_RE.search(video_url or "")
    return m.group(0) if m else None


def _page_number(video_url: str) -> int:
    try:
        query = parse_qs(urlparse(video_url or "").query)
        return max(1, int(query.get("p", ["1"])[0]))
    except Exception:
        return 1


def _pick_track(tracks: list, langs: Iterable[str] | None) -> dict | None:
    """按用户配置的语言顺序挑轨道, 挑不到就退到任何中文轨道。"""
    if not tracks:
        return None
    wanted = [str(item).lower() for item in (langs or ())]
    for want in wanted:
        for track in tracks:
            if str(track.get("lan", "")).lower() == want:
                return track
    for track in tracks:
        lan = str(track.get("lan", "")).lower()
        if "zh" in lan or lan.startswith("ai-"):
            return track
    return tracks[0]


async def fetch_subtitles(client, video_url: str, *, langs: Iterable[str] | None = None) -> TranscriptResult | None:
    """取 B 站官方/机器字幕; 没有轨道时返回 None(交给上层走 ASR)。"""
    bvid = _extract_bvid(video_url)
    if not bvid:
        return None

    view = await client.request_json("GET", _VIEW, params={"bvid": bvid})
    data = (view or {}).get("data") or {}
    pages = data.get("pages") or []
    if not pages:
        raise BiliVideoError(f"{bvid}: video has no pages")

    page = min(_page_number(video_url), len(pages))
    cid = pages[page - 1].get("cid")
    if not cid:
        raise BiliVideoError(f"{bvid}: page {page} has no cid")

    player = await client.request_json("GET", _PLAYER_V2, params={"bvid": bvid, "cid": cid})
    subtitle = ((player or {}).get("data") or {}).get("subtitle") or {}
    tracks = subtitle.get("subtitles") or []
    track = _pick_track(tracks, langs)
    if not track:
        logger.info(f"{bvid} p{page}: no subtitle tracks")
        return None

    url = str(track.get("subtitle_url") or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://api.bilibili.com" + url

    payload = await client.request_json("GET", url)
    body = (payload or {}).get("body") or []
    if not body:
        logger.info(f"{bvid} p{page}: subtitle body empty")
        return None

    segments = []
    for item in body:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        segments.append(TranscriptSegment(
            start=float(item.get("from") or 0.0),
            end=float(item.get("to") or 0.0),
            text=text,
        ))
    if not segments:
        return None

    # 内容校验: B 站签名字幕 URL 会命中共享缓存, 实测同一条视频连续抓 5 次能
    # 拿到 5 种内容(其中 3 次是别的视频)。唯一可靠的客观判据是"字幕时间轴必须
    # 覆盖整段视频": 真实字幕覆盖率 ~100%, 串台样本实测 5% / 78% / 571%。
    duration = float(pages[page - 1].get("duration") or data.get("duration") or 0)
    last_ts = max(seg.end for seg in segments)
    music_only = sum(1 for seg in segments if seg.text.strip("♪ ♫ 　").strip() == "")
    total = sum(len(seg.text.strip()) for seg in segments)
    coverage = last_ts / duration if duration > 0 else 0.0
    if total < 60 or music_only / len(segments) > 0.6 or not (0.88 <= coverage <= 1.12):
        logger.warning(
            f"{bvid} p{page}: subtitle rejected "
            f"(chars={total}, music={music_only}/{len(segments)}, "
            f"coverage={coverage:.0%} of {duration:.0f}s), fall back to ASR"
        )
        return None

    full_text = "\n".join(seg.text for seg in segments)
    logger.info(
        f"native subtitle hit {bvid} p{page} lan={track.get('lan')} "
        f"{len(segments)} 行 {len(full_text)} 字"
    )
    return TranscriptResult(
        language=str(track.get("lan") or "") or None,
        full_text=full_text,
        segments=tuple(segments),
        raw={"bvid": bvid, "cid": cid, "lan": track.get("lan"), "source": "player/v2"},
    )
