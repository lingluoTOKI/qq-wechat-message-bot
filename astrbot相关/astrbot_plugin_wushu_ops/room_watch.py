"""等待好友加入的同盟房间自动播报。

流程：定时读取 /api/admin/rooms → 找出未开赛、房主在线、真人未满的同盟房间 →
每个房间单独启动 3 秒倒计时 → 倒计时结束后重新拉取房间列表复核 → 仍可加入才把
房间号播报到配置的群（broadcast_targets）。

* 只处理 mode=coop 且 !inMatch 的房间；solo 房间别人进不去，不播。
* 同一房间连续处于等待状态只播一次；满员、开赛、关房后重新空出来才会再播。
* 所有网络/播报异常都在内部消化：单服失败只记一次日志，播报失败下一轮重试。
"""

from __future__ import annotations

import asyncio
import time

from .wushu_net import NetError, SERVER_MAIN, SERVER_ALLIANCE

FULL_SEATS = 6
DIFFICULTY_LABELS = {
    "FUNNY": "标准",
    "NORMAL": "险境",
    "HARD": "绝境",
    "ABYSS": "终极",
}
SERVER_ALIASES = {
    "主服": SERVER_MAIN,
    "main": SERVER_MAIN,
    "契约服": SERVER_ALLIANCE,
    "契约": SERVER_ALLIANCE,
    "联盟": SERVER_ALLIANCE,
    "alliance": SERVER_ALLIANCE,
}


def _logger():
    try:
        from astrbot.api import logger
        return logger
    except Exception:  # 单元测试环境没有宿主 logger
        import logging
        return logging.getLogger("wushu_ops.room_watch")


class RoomWatch:
    """轮询房间列表，给等待好友加入的同盟房间做一次倒计时播报。"""

    def __init__(self, runtime):
        self.r = runtime
        self._task = None
        self._countdowns = {}
        self._tracks = {}
        self._failing = set()
        self._disabled_warned = False
        self._missing_warned = False
        self._players_cache = None

    # ---------- 配置 ----------

    @property
    def config(self):
        return getattr(self.r.plugin, "config", {}) or {}

    def enabled(self) -> bool:
        return bool(self.config.get("room_watch_enable", True))

    def servers(self):
        """配置里可写「契约服」「契约服,主服」，默认只盯契约服。"""
        raw = str(self.config.get("room_watch_servers", SERVER_ALLIANCE) or "")
        names = []
        for part in raw.replace("，", ",").replace("、", ",").split(","):
            part = part.strip()
            name = SERVER_ALIASES.get(part) or SERVER_ALIASES.get(part.lower())
            if name and name not in names:
                names.append(name)
        return names or [SERVER_ALLIANCE]

    def targets(self):
        """房间播报专用目标；留空则沿用 broadcast_targets。"""
        raw = str(self.config.get("room_watch_targets", "") or "")
        targets = [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
        return targets or None

    def interval(self) -> float:
        try:
            return max(3.0, float(self.config.get("room_watch_interval_seconds", 10)))
        except (TypeError, ValueError):
            return 10.0

    def countdown(self) -> float:
        try:
            return max(0.0, float(self.config.get("room_watch_countdown_seconds", 3)))
        except (TypeError, ValueError):
            return 3.0

    def full_seats(self) -> int:
        try:
            return min(FULL_SEATS, max(2, int(self.config.get("room_watch_full_players", FULL_SEATS))))
        except (TypeError, ValueError):
            return FULL_SEATS

    def _api(self):
        return self.r.plugin.net.api

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if not self.enabled():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            _logger().info("[wushu_ops] 等待房间播报已启动：%s · 每 %.0f 秒检查 · 倒计时 %.0f 秒 · 满员 %s 人" % (
                "、".join(self.servers()), self.interval(), self.countdown(),
                self.full_seats()))

    async def stop(self) -> None:
        tasks = [task for task in [self._task, *self._countdowns.values()]
                 if task is not None and not task.done()]
        self._task = None
        self._countdowns.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger().error("[wushu_ops] 房间播报扫描异常：%s" % str(exc)[:160])
            try:
                await asyncio.sleep(self.interval())
            except asyncio.CancelledError:
                break

    # ---------- 扫描 ----------

    async def scan(self) -> None:
        if not self.enabled():
            if not self._disabled_warned:
                self._disabled_warned = True
                _logger().info("[wushu_ops] 等待房间播报已关闭（room_watch_enable=false）")
            return
        api = self._api()
        if not api.admin_ready():
            if not self._missing_warned:
                self._missing_warned = True
                _logger().warning("[wushu_ops] 未配置 admin_token，等待房间播报不可用")
            return
        self._missing_warned = False
        for server in self.servers():
            try:
                data = await api.admin_rooms(server)
            except NetError as exc:
                if server not in self._failing:
                    self._failing.add(server)
                    _logger().warning("[wushu_ops] %s 房间列表读取失败：%s" % (server, exc.user_message()))
                continue
            if server in self._failing:
                self._failing.discard(server)
                _logger().info("[wushu_ops] %s 房间列表读取已恢复" % server)
            self._reconcile(server, data.get("rooms") or [])

    def waiting(self, room) -> bool:
        """未开赛、房主在线、真人未满的同盟房间才算“正在等好友加入”。"""
        if not isinstance(room, dict) or not room.get("code"):
            return False
        if room.get("mode") != "coop" or room.get("inMatch"):
            return False
        try:
            humans = int(room.get("humanCount") or 0)
            connected = int(room.get("connectedHumans") or 0)
        except (TypeError, ValueError):
            return False
        return humans >= 1 and connected >= 1 and humans < self.full_seats()

    def _reconcile(self, server: str, rooms) -> None:
        waiting = {}
        for room in rooms:
            if self.waiting(room):
                item = dict(room)
                item["code"] = str(item.get("code")).upper()
                waiting[server + ":" + item["code"]] = item
        # 满员 / 开赛 / 关房：撤销倒计时，允许房间重新空出来后再次播报
        for key in list(self._tracks):
            if key.startswith(server + ":") and key not in waiting:
                self._drop(key)
        for key, room in waiting.items():
            track = self._tracks.get(key)
            if track is None:
                track = {"server": server, "code": room["code"], "announced": False}
                self._tracks[key] = track
            if track.get("announced") or key in self._countdowns:
                continue
            if time.time() < track.get("retry_at", 0):
                continue
            self._countdowns[key] = asyncio.create_task(
                self._countdown(key, server, room["code"]))

    def _drop(self, key: str) -> None:
        self._tracks.pop(key, None)
        task = self._countdowns.pop(key, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    # ---------- 倒计时与播报 ----------

    async def _countdown(self, key: str, server: str, code: str) -> None:
        try:
            await asyncio.sleep(self.countdown())
            track = self._tracks.get(key)
            if track is None or track.get("announced"):
                return
            # 倒计时期间可能已经满员、开赛或被关闭，播报前重新拉一次真实状态
            try:
                data = await self._api().admin_rooms(server)
            except NetError as exc:
                track["retry_at"] = time.time() + 30
                _logger().warning("[wushu_ops] %s 房间 %s 播报前复核失败：%s" % (
                    server, code, exc.user_message()))
                return
            room = next((dict(item) for item in (data.get("rooms") or [])
                         if str(item.get("code") or "").upper() == code
                         and self.waiting(item)), None)
            if room is None:
                self._drop(key)
                return
            # 房主掉线超过宽限期就不再是“在等好友”，不播；查询失败时不拦截
            host, online = await self._host_info(server, room.get("hostId"))
            if online is False:
                self._drop(key)
                return
            text = await self._message(server, room, host)
            targets = self.targets()
            sent, failed = await self.r.plugin._broadcast(text, targets=targets)
            if not sent:
                track["retry_at"] = time.time() + 60
                _logger().warning("[wushu_ops] 房间 %s 播报未送达：%s" % (
                    code, "、".join(failed) or "未配置播报目标"))
                return
            track["announced"] = True
            track["announced_at"] = time.time()
            _logger().info("[wushu_ops] 已播报 %s 房间 %s（%s/%s 人），送达 %s 个群" % (
                server, code, room.get("humanCount", 1), self.full_seats(), len(sent)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger().error("[wushu_ops] 房间 %s 播报异常：%s" % (code, str(exc)[:160]))
        finally:
            if self._countdowns.get(key) is asyncio.current_task():
                self._countdowns.pop(key, None)

    async def _message(self, server: str, room, host: str = "") -> str:
        code = str(room.get("code") or "").upper()
        try:
            humans = int(room.get("humanCount") or 1)
        except (TypeError, ValueError):
            humans = 1
        difficulty = DIFFICULTY_LABELS.get(str(room.get("difficulty") or "").upper(),
                                           str(room.get("difficulty") or ""))
        fields = [server, "%d/%d" % (humans, self.full_seats())]
        if difficulty:
            fields.append(difficulty)
        tip = ("%s在等" % host) if host else "房主在等"
        return "\U0001F41F %s — %s，加入房间输 %s" % (
            " · ".join(fields), tip, code)

    async def _host_info(self, server: str, host_id):
        """返回 (房主昵称, 是否在线)；玩家列表查询失败时在线状态为 None（不拦截播报）。"""
        if not host_id:
            return "", None
        cache = self._players_cache
        now = time.time()
        if cache and cache[0] == server and now - cache[1] < 30:
            names = cache[2]
        else:
            try:
                data = await self._api().admin_players(server)
            except NetError:
                return "", None
            names = {str(p.get("playerId")): str(p.get("name") or "")[:16]
                     for p in (data.get("players") or []) if p.get("playerId")}
            self._players_cache = (server, now, names)
        key = str(host_id)
        return names.get(key, ""), key in names

    # ---------- 查询 ----------

    async def describe(self, server=None) -> str:
        if not self.enabled():
            return "等待房间播报当前已关闭（配置项 room_watch_enable）。"
        api = self._api()
        if not api.admin_ready():
            return "未配置管理令牌（admin_token），无法读取房间列表，等待房间播报不可用。"
        lines = []
        for name in ([server] if server else self.servers()):
            try:
                data = await api.admin_rooms(name)
            except NetError as exc:
                lines.append("%s ❌ 房间列表读取失败：%s" % (name, exc.user_message()))
                continue
            rooms = [room for room in (data.get("rooms") or []) if self.waiting(room)]
            if not rooms:
                lines.append("%s 当前没有等待好友加入的同盟房间。" % name)
                continue
            lines.append("%s 等待好友加入的同盟房间 %s 个：" % (name, len(rooms)))
            for room in rooms[:10]:
                key = name + ":" + str(room.get("code") or "").upper()
                track = self._tracks.get(key) or {}
                mark = "已播报" if track.get("announced") else (
                    "倒计时中" if key in self._countdowns else "待播报")
                lines.append("· %s  %s/%s 人 · %s" % (
                    str(room.get("code") or "?").upper(),
                    room.get("humanCount", 0), self.full_seats(), mark))
            if len(rooms) > 10:
                lines.append("…另有 %d 个未列出" % (len(rooms) - 10))
        targets = self.targets()
        lines.append("播报目标：%s；每 %d 秒检查一次，倒计时 %d 秒。" % (
            ("、".join(targets) if targets else "沿用 broadcast_targets"),
            int(self.interval()), int(self.countdown())))
        return "\n".join(lines)
