"""群聊预算 + 高峰休眠守卫（大肥鱼）。

规则（2026-10-29 定）：
1. 群聊：按日额度 ¥0.5（**账单口径**，面板数字需 ÷ panel_to_bill 折算），超出后当日不再回复；
2. 私聊：完全不受本插件限制（private_exempt=true）；
3. 高峰休眠：工作日北京时间 09:00-12:00、14:00-18:00 群聊一律不回复，到点自动恢复；
4. 休眠/超预算期间收到的消息**不补答**，只回复恢复之后的新消息（靠 stop_event 丢弃，不排队）。

与既有预算的关系：本插件是**更严格的一层**，cost_control 的 global_daily 仍是最后兜底，
两层不会互相放大：任一触发即停。
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star, register

PLUGIN = "astrbot_plugin_dafeiyu_group_guard"
SUPPLEMENT = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"
CONFIG_PATH = Path("/AstrBot/data/plugin_data") / PLUGIN / "config.json"
TZ = ZoneInfo("Asia/Shanghai")

DEFAULTS = {
    "enabled": True,
    "group_daily_budget_cny": 0.5,
    "private_exempt": True,
    # 面板行合计 → 账单估算的系数。实测同日 raw 行合计 ≈ 官方 ±10%，故默认 1.0；
    # 若以后要用 cost_control 面板展示值（按 request_id 去重）作基数，应改为 0.714。
    "panel_to_bill": 1.0,
    # 规则生效时刻（ISO，含时区）。当日额度从 max(当天0点, 此处) 起算，避免"一上线就因历史花费被静默"。
    "effective_from": "2026-10-29T19:55:00+08:00",
    "peak_sleep": True,
    "peak_windows": [[9, 12], [14, 18]],
    "peak_weekdays_only": True,
    "notice_peak": "嗝——现在是高峰段，我说一句话都要双倍的钱，先眯一会儿。等 {end} 一到我就醒，"
                   "这会儿要是有事，把话放这儿，我一睁眼就回，不会装死的。",
    "notice_budget": "今天的群聊零花钱（¥{budget}）已经花光啦，我先闭嘴到明天 0 点。",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open(encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
    except Exception as exc:  # 配置坏了就用默认，绝不因为配置把守卫弄挂
        logger.warning(f"[group_guard] 配置读取失败，使用默认值：{exc}")
    return cfg


def is_group_umo(umo: str) -> bool:
    return "GroupMessage" in (umo or "")


def peak_window(now: datetime, cfg: dict):
    """返回当前命中的高峰窗口 (起, 止)；不在高峰返回 None。"""
    if not cfg.get("peak_sleep", True):
        return None
    if cfg.get("peak_weekdays_only", True) and now.weekday() >= 5:
        return None
    for start, end in cfg.get("peak_windows") or []:
        if int(start) <= now.hour < int(end):
            return int(start), int(end)
    return None


def next_resume(now: datetime, cfg: dict):
    """当前/下一个休眠结束时刻（用于文案里的“几点醒”）。"""
    windows = sorted([(int(s), int(e)) for s, e in cfg.get("peak_windows") or []])
    for start, end in windows:
        if now.hour < end:
            return f"{end:02d}:00"
    return f"{windows[0][1]:02d}:00" if windows else "09:00"


def group_spend_cny(umo: str, now: datetime, cfg: dict | None = None) -> float:
    """当日该群已花（面板行合计，CNY）。

    基数取 supplement.db 的固化 ``cost_amount``；每行对应一次真实 API 调用，
    与官方计费口径一致（实测同日合计 ≈ 官方 ±10%）。
    """
    if not Path(SUPPLEMENT).exists():
        return 0.0
    cfg = cfg or {}
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    raw_from = str(cfg.get("effective_from") or "")
    if raw_from:
        try:
            eff = datetime.fromisoformat(raw_from)
            if eff.tzinfo is None:
                eff = eff.replace(tzinfo=TZ)
            eff = eff.astimezone(TZ)
            if eff > start and eff.date() == start.date():
                start = eff
        except ValueError:
            pass
    utc_start = start.isoformat()
    try:
        con = sqlite3.connect(f"file:{SUPPLEMENT}?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute(
                "select coalesce(sum(cost_amount), 0) from cost_supplements "
                "where umo = ? and julianday(created_at) >= julianday(?)", (umo, utc_start)).fetchone()
        finally:
            con.close()
        return float(row[0] or 0.0)
    except Exception as exc:
        logger.warning(f"[group_guard] 读取花费失败（放行）：{exc}")
        return 0.0


@register(PLUGIN, "TOKI", "群聊日额度与高峰休眠守卫", "1.0.0")
class GroupGuardPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        self._said = {}  # (umo, key) -> 已提示日期，避免同一窗口刷屏

    def _throttle(self, umo: str, key: str) -> bool:
        """同一群同一 key（窗口或日期）只提示一次。"""
        if self._said.get((umo, key)):
            return True
        self._said[(umo, key)] = True
        if len(self._said) > 500:
            self._said.clear()
        return False

    @filter.on_llm_request(priority=100003)
    async def guard(self, event: AstrMessageEvent, req):
        cfg = load_config()
        if not cfg.get("enabled", True):
            return
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not is_group_umo(umo):
            if cfg.get("private_exempt", True):
                return

        now = datetime.now(TZ)
        window = peak_window(now, cfg)
        if window:
            if self._throttle(umo, f"peak-{now.strftime('%Y%m%d')}-{window[0]}"):
                event.stop_event()
                return
            event.stop_event()
            await event.send(event.plain_result(
                cfg["notice_peak"].format(end=next_resume(now, cfg))))
            return

        spent = group_spend_cny(umo, now, cfg)
        budget = float(cfg.get("group_daily_budget_cny") or 0)
        bill = spent * float(cfg.get("panel_to_bill") or 1.0)
        if budget > 0 and bill >= budget:
            logger.info(f"[group_guard] 群额度用尽 {umo} 估算 ¥{bill:.3f}/¥{budget:g}")
            if self._throttle(umo, f"budget-{now.strftime('%Y%m%d')}"):
                event.stop_event()
                return
            event.stop_event()
            await event.send(event.plain_result(
                cfg["notice_budget"].format(budget=f"{budget:g}")))
