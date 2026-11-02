"""群聊预算 + 高峰休眠守卫（大肥鱼）。

规则（2026-10-29 定）：
1. 群聊：按日额度 ¥0.5（**账单口径**，本地记录的金额 × panel_to_bill 折算），超出后当日不再回复；
2. 私聊：完全不受本插件限制（private_exempt=true）；
3. 高峰休眠：工作日北京时间 09:00-12:00、14:00-18:00 群聊一律不回复，到点自动恢复；
4. 休眠期收到的消息**进队列，醒来后补答**：12:00 / 18:00 恢复时按群生成一段回复；
   超过 ``peak_queue_max_age_minutes`` 的旧消息丢弃；超预算期间仍然不补答。

与既有预算的关系：本插件是**更严格的一层**，cost_control 的 global_daily 仍是最后兜底，
两层不会互相放大：任一触发即停。
"""

import asyncio
import json
import random
import re
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
PERSONA_DB = Path("/AstrBot/data/data_v4.db")
TZ = ZoneInfo("Asia/Shanghai")

# 补答时的任务说明；人设另外从 personas 表读取
CATCHUP_TASK = (
    "你刚才在群里高峰休眠，期间没有开口；现在刚醒。下面按时间顺序列出休眠期间收到的消息。"
    "请像平时聊天一样自然回应：\n"
    "1. 先用一句话表示你醒了/回来了，再回应消息里真正需要你的内容；\n"
    "2. 一次说完，不要逐条引用、不要编号列表、不要分多条；\n"
    "3. 不重要的闲聊轻轻带过，别硬凑；\n"
    "4. 绝对不要提预算、费用、高峰、休眠、插件、延迟回复这些词；\n"
    "5. 直接输出要发到群里的正文，不要加引号或前缀。"
)
STYLE_FALLBACK = "你是小洛，蓝发小鲸鱼娘，QQ群里的普通群友，傲娇嘴硬心软，说话短、像随手打字。"
AT_PREFIX = re.compile(r"^(?:(?:\[At:[^\]]*\]|\[CQ:at,[^\]]*\]|<@!?[^>]+>)\s*)+")

DEFAULTS = {
    "enabled": True,
    "group_daily_budget_cny": 0.5,
    "private_exempt": True,
    # 本地行合计 → 官方账单的折算系数。
    # 2026-10-29 实测：官方 114 请求 ¥1.261369，supplement 76 行 ¥0.827703，
    # 覆盖率 65.6% → 系数 1.524。cost_control 只记录经过其 hook 的请求，
    # 压缩、内部摘要等调用不在表内；不折算的话 ¥0.5 额度实际会花到约 ¥0.76。
    "panel_to_bill": 1.52,
    # 规则生效时刻（ISO，含时区）。当日额度从 max(当天0点, 此处) 起算，避免"一上线就因历史花费被静默"。
    "effective_from": "2026-10-29T19:55:00+08:00",
    "peak_sleep": True,
    "peak_windows": [[9, 12], [14, 18]],
    "peak_weekdays_only": True,
    # 休眠期消息缓存：醒来（12:00 / 18:00）后按群补一段回复。
    "peak_queue": True,
    "peak_queue_max_messages": 20,
    "peak_queue_max_age_minutes": 300,
    "peak_queue_poll_seconds": 30,
    # 补答用的模型与人设：provider 留空则用当前会话的默认模型。
    "catchup_provider_id": "",
    "catchup_persona_id": "12",
    "catchup_max_tokens": 800,
    "catchup_timeout_seconds": 60,
    # 高峰拒绝文案：列表形式，每次随机取一条，避免来来去去一句话像机器人。
    # 支持 {end}（几点醒）占位符；想加/改就直接塞字符串进这个数组。
    "notice_peak": [
        "嗝——现在是高峰段，说句话都要双倍的钱，我先眯会儿，等 {end} 就醒，有事把话放这儿。",
        "困……又到这个贵死人的点了，本鲸先潜水，{end} 再冒泡，回见。",
        "这个点开口就是给平台白打工，溜了溜了，{end} 我再来。",
        "（打了个哈欠）高峰段烧钱，我待机到 {end}，真有事别只喊我一声啊。",
        "嘶——双倍价格太肉疼，我先装死到 {end}，不会真不回来的。",
    ],
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
    """当日该群已花（账单口径估算，CNY）。

    基数取 supplement.db 的 ``cost_amount``（每次 API 调用的官方单价算出的金额，
    含峰时 ×2），再乘以 ``panel_to_bill`` 补足未记录的调用（实测覆盖率约 65.6%）。
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


@register(PLUGIN, "TOKI", "群聊日额度与高峰休眠守卫", "1.1.0")
class GroupGuardPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        self._said = {}  # (umo, key) -> 已提示日期，避免同一窗口刷屏
        self._queue = {}  # umo -> {"items": [...], "dropped": int}
        self._queue_lock = asyncio.Lock()
        self._flush_task = None

    async def initialize(self):
        try:
            self._flush_task = asyncio.create_task(self._flush_loop())
        except Exception as exc:
            logger.warning(f"[group_guard] 补答后台任务启动失败：{str(exc)[:120]}")

    async def terminate(self):
        task, self._flush_task = self._flush_task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @staticmethod
    def _pick_notice(cfg: dict, key: str, **fmt) -> str:
        """取一条拒绝文案：列表随机挑，旧版单字符串也能用，再填充占位符。"""
        raw = cfg.get(key, "")
        if isinstance(raw, (list, tuple)):
            options = [str(x) for x in raw if str(x).strip()]
            raw = random.choice(options) if options else ""
        raw = str(raw or "").strip()
        if not raw:
            return ""
        try:
            return raw.format(**fmt)
        except (KeyError, IndexError):
            return raw

    def _throttle(self, umo: str, key: str) -> bool:
        """同一群同一 key（窗口或日期）只提示一次。"""
        if self._said.get((umo, key)):
            return True
        self._said[(umo, key)] = True
        if len(self._said) > 500:
            self._said.clear()
        return False

    def _enqueue(self, umo: str, event: AstrMessageEvent, now: datetime, cfg: dict):
        """休眠期把「本来要回」的消息存进队列，醒来一次性补答。"""
        if not cfg.get("peak_queue", True):
            return
        text = AT_PREFIX.sub("", str(getattr(event, "message_str", "") or "")).strip()
        if not text or text.startswith(("/", "／")):
            return
        mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        slot = self._queue.setdefault(umo, {"items": [], "dropped": 0})
        if mid and any(item.get("mid") == mid for item in slot["items"]):
            return
        name = str(getattr(event, "get_sender_name", lambda: "")() or "").strip() or "群友"
        slot["items"].append({"name": name[:32], "text": text[:300], "ts": now, "mid": mid})
        cap = max(1, int(cfg.get("peak_queue_max_messages") or 20))
        while len(slot["items"]) > cap:
            slot["items"].pop(0)
            slot["dropped"] += 1

    async def _flush_loop(self):
        await asyncio.sleep(15)  # 等 AstrBot 自身启动完成
        while True:
            cfg = load_config()
            try:
                now = datetime.now(TZ)
                if cfg.get("enabled", True) and self._queue and not peak_window(now, cfg):
                    await self._flush(now, cfg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[group_guard] 补答循环异常：{str(exc)[:200]}")
            try:
                poll = max(5.0, float(cfg.get("peak_queue_poll_seconds") or 30))
            except (TypeError, ValueError):
                poll = 30.0
            await asyncio.sleep(poll)

    async def _flush(self, now: datetime, cfg: dict):
        """休眠结束后，按群把缓存的消息合成一段回复补发。"""
        if self._queue_lock.locked():
            return
        async with self._queue_lock:
            for umo in list(self._queue):
                now = datetime.now(TZ)
                if peak_window(now, cfg):
                    return
                slot = self._queue.pop(umo, None)
                if not slot or not is_group_umo(umo):
                    continue
                ttl = timedelta(minutes=float(cfg.get("peak_queue_max_age_minutes") or 300))
                items = [item for item in slot["items"] if now - item["ts"] <= ttl]
                if not items:
                    continue
                spent = group_spend_cny(umo, now, cfg)
                budget = float(cfg.get("group_daily_budget_cny") or 0)
                bill = spent * float(cfg.get("panel_to_bill") or 1.0)
                if budget > 0 and bill >= budget:
                    logger.info(f"[group_guard] 醒来补答跳过（额度已满） {umo} "
                                f"估算 ¥{bill:.3f}/¥{budget:g}")
                    continue
                try:
                    text = await self._compose(umo, items, int(slot.get("dropped") or 0), now, cfg)
                except Exception as exc:
                    logger.warning(f"[group_guard] 补答生成失败 {umo}：{str(exc)[:160]}")
                    continue
                if not text:
                    continue
                if peak_window(datetime.now(TZ), cfg):
                    # 生成耗时跨进了下一个高峰窗口：放回队列，等真正平峰再补。
                    pending = self._queue.setdefault(umo, {"items": [], "dropped": 0})
                    pending["items"][0:0] = slot["items"]
                    pending["dropped"] = int(pending.get("dropped") or 0) + int(slot.get("dropped") or 0)
                    return
                ok = await self._send(umo, text)
                logger.info(f"[group_guard] 醒来补答 {umo} {len(items)} 条 → "
                            f"{'已发送' if ok else '发送失败'}")
                await asyncio.sleep(1.5)

    async def _compose(self, umo: str, items: list, dropped: int, now: datetime, cfg: dict) -> str:
        lines = []
        if dropped:
            lines.append(f"（更早还有 {dropped} 条没列出来）")
        for item in items:
            lines.append(f"{item['ts'].strftime('%H:%M')} {item['name']}：{item['text']}")
        prompt = (f"现在是 {now.strftime('%H:%M')}。休眠期间收到的消息：\n"
                  + "\n".join(lines) + "\n\n请直接输出一段群聊回复。")
        system_prompt = self._persona_prompt(cfg) + "\n\n" + CATCHUP_TASK
        provider_id = str(cfg.get("catchup_provider_id") or "").strip()
        try:
            max_tokens = int(cfg.get("catchup_max_tokens") or 800)
            timeout = float(cfg.get("catchup_timeout_seconds") or 60)
        except (TypeError, ValueError):
            max_tokens, timeout = 800, 60.0
        text = await self._ask(umo, provider_id, system_prompt, prompt, max_tokens, timeout)
        return text.strip().strip("\"“”").strip()[:600]

    def _persona_prompt(self, cfg: dict) -> str:
        """读 AstrBot 里的人格；读不到就用兜底口吻。"""
        persona_id = str(cfg.get("catchup_persona_id", "12") or "12")
        try:
            if PERSONA_DB.exists():
                con = sqlite3.connect(f"file:{PERSONA_DB}?mode=ro", uri=True, timeout=3)
                try:
                    row = con.execute(
                        "select system_prompt from personas where persona_id=?",
                        (persona_id,)).fetchone()
                finally:
                    con.close()
                if row and row[0]:
                    return str(row[0]).strip()
        except Exception as exc:
            logger.warning(f"[group_guard] 读取人格 {persona_id} 失败：{str(exc)[:120]}")
        return STYLE_FALLBACK

    @staticmethod
    def _extract_text(resp) -> str:
        if resp is None:
            return ""
        if isinstance(resp, str):
            return resp
        for attr in ("completion_text", "text", "content"):
            value = getattr(resp, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(resp, dict):
            for key in ("completion_text", "text", "content"):
                value = resp.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return str(resp)

    async def _ask(self, umo: str, provider_id: str, system_prompt: str,
                   prompt: str, max_tokens: int, timeout: float) -> str:
        async def _run():
            if provider_id and hasattr(self.context, "llm_generate"):
                try:
                    resp = await self.context.llm_generate(
                        chat_provider_id=provider_id, prompt=prompt,
                        system_prompt=system_prompt, max_tokens=max_tokens)
                except TypeError:
                    resp = await self.context.llm_generate(
                        chat_provider_id=provider_id, prompt=prompt,
                        system_prompt=system_prompt)
                text = self._extract_text(resp)
                if text.strip():
                    return text
            provider = None
            if provider_id and hasattr(self.context, "get_provider_by_id"):
                provider = self.context.get_provider_by_id(provider_id)
            if provider is None and hasattr(self.context, "get_using_provider"):
                provider = self.context.get_using_provider()
            if provider is None or not hasattr(provider, "text_chat"):
                raise RuntimeError("没有可用的模型 Provider")
            try:
                resp = await provider.text_chat(
                    system_prompt=system_prompt, prompt=prompt, max_tokens=max_tokens)
            except TypeError:
                resp = await provider.text_chat(
                    system_prompt=system_prompt, prompt=prompt)
            text = self._extract_text(resp)
            if not text.strip():
                raise RuntimeError("模型返回空文本")
            return text

        return await asyncio.wait_for(_run(), timeout=timeout)

    async def _send(self, umo: str, text: str) -> bool:
        try:
            from dafeiyu_runtime.delivery import send_text
            await send_text(self.context, umo, text, timeout=20)
            return True
        except Exception as exc:
            logger.debug(f"[group_guard] send_text 不可用：{str(exc)[:120]}")
        try:
            from astrbot.api.message_components import MessageChain, Plain
            await self.context.send_message(umo, MessageChain([Plain(text)]))
            return True
        except Exception as exc:
            logger.warning(f"[group_guard] 补答发送失败 {umo}：{str(exc)[:160]}")
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
            try:
                self._enqueue(umo, event, now, cfg)
            except Exception as exc:  # 队列出错也不能影响休眠本身
                logger.warning(f"[group_guard] 缓存消息失败：{str(exc)[:120]}")
            if self._throttle(umo, f"peak-{now.strftime('%Y%m%d')}-{window[0]}"):
                event.stop_event()
                return
            event.stop_event()
            await event.send(event.plain_result(
                self._pick_notice(cfg, "notice_peak", end=next_resume(now, cfg))))
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
                self._pick_notice(cfg, "notice_budget", budget=f"{budget:g}")))
