"""卫戍协议服务端通信统一封装（HTTP + WebSocket）。

对外只暴露 NetworkManager / GameApi / LocalStatusApi / GameSocketClient：

* HTTP：统一超时、指数退避重试、错误分类、响应大小与 JSON 校验。
* WebSocket：按 shared/protocol.js 的 `{t, rid, ...}` 文本帧收发，
  hello→welcome、ping→pong、请求用 rid 等待 ok/error，支持心跳与重连。
* 管理接口：/api/admin/* 需要 admin_token（对应服务端 SP_ADMIN_TOKEN），
  未配置时直接抛 NetAuthError，不会发出请求。

接口来源：游戏仓库 server/http/routes.js、server/net.js、shared/protocol.js。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional

import aiohttp

DEFAULT_MAIN_BASE = "https://game.lingluo-toki.top"
DEFAULT_ALLIANCE_BASE = "https://alliance.lingluo-toki.top"
DEFAULT_LOCAL_BASE = "http://172.18.0.1:31999"

SERVER_MAIN = "主服"
SERVER_ALLIANCE = "契约服"
SERVERS = (SERVER_MAIN, SERVER_ALLIANCE)

# 服务端 shared/constants.js
PROTOCOL_VERSION = 1
ROOM_CODE_LEN = 4
NAME_MAX_LEN = 12
DIFFICULTIES = frozenset({"FUNNY", "NORMAL", "HARD", "ABYSS"})

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
ROOM_CODE_RE = re.compile(r"^[A-Za-z0-9]{%d,%d}$" % (ROOM_CODE_LEN, ROOM_CODE_LEN + 2))


# --------------------------------------------------------------------------- 错误


class NetError(Exception):
    """所有网络层错误的基类。user_message() 可直接给玩家/管理员看。"""

    code = "NET"

    def __init__(self, message: str, *, status: Optional[int] = None,
                 detail: str = "", url: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail
        self.url = url

    def user_message(self) -> str:
        return self.message

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        extra = []
        if self.status is not None:
            extra.append("HTTP %s" % self.status)
        if self.detail:
            extra.append(self.detail)
        return self.message + ("（%s）" % " · ".join(extra) if extra else "")


class NetTimeout(NetError):
    code = "TIMEOUT"


class NetUnavailable(NetError):
    code = "UNAVAILABLE"


class NetProtocolError(NetError):
    code = "PROTOCOL"


class NetHttpError(NetError):
    code = "HTTP"


class NetAuthError(NetHttpError):
    code = "AUTH"


class NetNotFound(NetHttpError):
    code = "NOT_FOUND"


class NetConflict(NetHttpError):
    code = "CONFLICT"


class NetRateLimited(NetHttpError):
    code = "RATE_LIMITED"


class SocketError(NetError):
    code = "WS"

    def __init__(self, message: str, *, error_code: str = "", rid: Any = None,
                 detail: str = ""):
        super().__init__(message, detail=detail)
        self.error_code = error_code
        self.rid = rid


# --------------------------------------------------------------------------- 重试


@dataclass(frozen=True)
class RetryPolicy:
    """指数退避 + 抖动；只对幂等请求和可重试状态码生效。"""

    attempts: int = 3
    base_delay: float = 0.4
    max_delay: float = 5.0
    jitter: float = 0.2
    retry_statuses: frozenset = RETRY_STATUSES

    def delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        if retry_after is not None:
            return max(0.0, min(self.max_delay, retry_after))
        raw = min(self.max_delay, self.base_delay * (2 ** max(0, attempt)))
        return raw * (1.0 + random.random() * self.jitter)


NO_RETRY = RetryPolicy(attempts=1)
FAST_RETRY = RetryPolicy(attempts=2, base_delay=0.2, max_delay=1.0)


# --------------------------------------------------------------------------- HTTP


@dataclass
class RawResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str = ""


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    value = (headers.get("retry-after") or "").strip()
    if value.isdigit():
        return float(value)
    return None


class HttpTransport:
    """可注入 session_factory / _perform 的 HTTP 传输层（测试无需真实网络）。"""

    def __init__(self, *, timeout: float = 8.0, retry: Optional[RetryPolicy] = None,
                 max_bytes: int = 2 << 20, session_factory: Optional[Callable[[], Any]] = None):
        self.timeout = float(timeout)
        self.retry = retry or RetryPolicy()
        self.max_bytes = max_bytes
        self._session_factory = session_factory
        self._session = None
        self._closed = False
        self.stats = {"requests": 0, "retries": 0, "failures": 0, "bytes": 0}

    async def _get_session(self):
        if self._closed:
            raise NetUnavailable("网络层已关闭")
        if self._session is None or getattr(self._session, "closed", False):
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                self._session = aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                )
        return self._session

    async def _perform(self, method: str, url: str, *, params: Optional[Mapping[str, Any]],
                       json_body: Optional[Any], headers: Optional[Mapping[str, str]],
                       timeout: float) -> RawResponse:
        session = await self._get_session()
        async with session.request(
            method, url, params=params, json=json_body, headers=headers,
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            body = await response.read()
            return RawResponse(
                status=int(response.status),
                headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                body=body,
                url=str(response.url),
            )

    def _decode(self, raw: RawResponse) -> Any:
        if len(raw.body) > self.max_bytes:
            raise NetProtocolError("响应过大（%d 字节）" % len(raw.body), url=raw.url)
        if not raw.body:
            return None
        try:
            text = raw.body.decode("utf-8-sig")
            return json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetProtocolError("响应不是合法 JSON：%s" % str(exc)[:80], url=raw.url) from exc

    @staticmethod
    def _http_error(raw: RawResponse, data: Any, url: str) -> NetError:
        detail = ""
        if isinstance(data, Mapping):
            detail = str(data.get("error") or data.get("detail") or data.get("hint") or "")[:160]
        cls = {401: NetAuthError, 403: NetAuthError, 404: NetNotFound,
               409: NetConflict, 429: NetRateLimited}.get(raw.status, NetHttpError)
        label = {401: "未授权", 403: "拒绝访问", 404: "接口不存在",
                 409: "状态冲突", 429: "请求过于频繁"}.get(raw.status, "服务器返回错误")
        return cls("%s（HTTP %d）%s" % (label, raw.status, ("：" + detail) if detail else ""),
                   status=raw.status, detail=detail, url=url)

    async def request(self, method: str, url: str, *, params: Optional[Mapping[str, Any]] = None,
                      json_body: Optional[Any] = None, headers: Optional[Mapping[str, str]] = None,
                      timeout: Optional[float] = None, retry: Optional[RetryPolicy] = None,
                      expect_json: bool = True) -> Any:
        """发请求并返回解析后的 JSON；失败抛 NetError 子类。"""
        policy = retry or self.retry
        method = method.upper()
        idempotent = method in ("GET", "HEAD")
        if not idempotent:
            policy = RetryPolicy(attempts=1)
        total_timeout = float(timeout or self.timeout)
        last: Optional[NetError] = None
        for attempt in range(max(1, policy.attempts)):
            self.stats["requests"] += 1
            try:
                raw = await asyncio.wait_for(
                    self._perform(method, url, params=params, json_body=json_body,
                                  headers=headers, timeout=total_timeout),
                    timeout=total_timeout + 1.0,
                )
            except asyncio.TimeoutError:
                last = NetTimeout("请求超时（%.0f 秒）" % total_timeout, url=url)
            except (aiohttp.ClientError, OSError) as exc:
                last = NetUnavailable("无法连接服务器：%s" % (str(exc)[:120] or type(exc).__name__),
                                      url=url)
            else:
                self.stats["bytes"] += len(raw.body)
                retryable = raw.status in policy.retry_statuses and attempt + 1 < policy.attempts
                if retryable:
                    self.stats["retries"] += 1
                    await asyncio.sleep(policy.delay(attempt, _retry_after(raw.headers)))
                    continue
                data = self._decode(raw) if expect_json else raw.body
                if raw.status >= 400:
                    raise self._http_error(raw, data, url)
                return data
            if attempt + 1 >= policy.attempts:
                break
            self.stats["retries"] += 1
            await asyncio.sleep(policy.delay(attempt))
        self.stats["failures"] += 1
        raise last or NetError("请求失败", url=url)

    async def close(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        if session is not None and not getattr(session, "closed", False):
            try:
                await session.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- 游戏 API


def _as_mapping(data: Any, what: str, url: str = "") -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise NetProtocolError("%s 返回格式异常" % what, url=url)
    return data


def _ok(data: Any, what: str, url: str = "") -> Mapping[str, Any]:
    data = _as_mapping(data, what, url)
    if data.get("ok") is not True:
        raise NetProtocolError("%s 未就绪" % what, url=url)
    return data


class GameApi:
    """游戏服 HTTP 接口（公开只读 + 管理运维）。"""

    def __init__(self, transport: HttpTransport, main_base: str = DEFAULT_MAIN_BASE,
                 alliance_base: str = DEFAULT_ALLIANCE_BASE, admin_token: str = ""):
        self.transport = transport
        self.main_base = (main_base or DEFAULT_MAIN_BASE).rstrip("/")
        self.alliance_base = (alliance_base or DEFAULT_ALLIANCE_BASE).rstrip("/")
        self.admin_token = (admin_token or "").strip()

    # ---- 基础 ----

    def base(self, server: str = SERVER_MAIN) -> str:
        return self.alliance_base if server in (SERVER_ALLIANCE, "alliance") else self.main_base

    def url(self, path: str, server: str = SERVER_MAIN) -> str:
        return self.base(server) + (path if path.startswith("/") else "/" + path)

    def admin_ready(self) -> bool:
        return bool(self.admin_token)

    def _admin_params(self, **extra: Any) -> Mapping[str, Any]:
        if not self.admin_token:
            raise NetAuthError("未配置管理令牌（admin_token），管理指令不可用")
        return {**{k: v for k, v in extra.items() if v is not None}, "token": self.admin_token}

    async def raw_get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                      server: str = SERVER_MAIN, timeout: Optional[float] = None,
                      retry: Optional[RetryPolicy] = None) -> Any:
        return await self.transport.request("GET", self.url(path, server), params=params,
                                            timeout=timeout, retry=retry)

    # ---- 配置 / 状态拉取 ----

    async def health(self, server: str = SERVER_MAIN, *, traffic: bool = False,
                     timeout: Optional[float] = None,
                     retry: Optional[RetryPolicy] = None) -> Mapping[str, Any]:
        params = {"traffic": "1"} if traffic else None
        return _as_mapping(await self.raw_get("/healthz", params=params, server=server,
                                              timeout=timeout, retry=retry),
                           "%s 健康检查" % server)

    async def health_ok(self, server: str = SERVER_MAIN, timeout: Optional[float] = None,
                        retry: Optional[RetryPolicy] = None) -> bool:
        data = await self.health(server, timeout=timeout, retry=retry)
        return data.get("ok") is True and bool(data.get("app"))

    async def online(self, server: str = SERVER_MAIN, *, timeout: Optional[float] = None) -> Mapping[str, Any]:
        # /api/online 只返回 {online, humans}，没有 ok 字段（routes.js），因此不能用 _ok 校验
        data = _as_mapping(await self.raw_get("/api/online", server=server, timeout=timeout),
                           "%s 在线人数" % server)
        for key in ("online", "humans"):
            if key in data and not isinstance(data[key], (int, float)):
                raise NetProtocolError("%s 在线人数字段 %s 格式异常" % (server, key))
        return data

    async def announcements(self, server: str = SERVER_MAIN, *,
                            timeout: Optional[float] = None) -> Mapping[str, Any]:
        return _as_mapping(await self.raw_get("/api/announcements", server=server, timeout=timeout),
                           "%s 公告" % server)

    async def daily_challenges(self, division: str = "solo", *, ranking: bool = False,
                               server: str = SERVER_MAIN,
                               timeout: Optional[float] = None) -> Mapping[str, Any]:
        if division not in ("solo", "coop"):
            raise NetProtocolError("division 只能是 solo 或 coop")
        params = {"division": division, "ranking": "1" if ranking else "0"}
        return _ok(await self.raw_get("/api/daily-challenges", params=params, server=server,
                                      timeout=timeout), "每日挑战")

    async def versions(self, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        """从 /healthz 提取版本与内容集合（避免再去 /api/* 拉重复数据）。"""
        data = await self.health(server)
        return {
            "app": data.get("app"),
            "protocol": data.get("version"),
            "build": data.get("build"),
            "content": data.get("content") or {},
            "versions": data.get("versions") or {},
        }

    # ---- 管理运维（GET + token，与 routes.js 一致）----

    async def admin_players(self, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        return _ok(await self.raw_get("/api/admin/players", params=self._admin_params(),
                                      server=server, timeout=8),
                   "玩家列表")

    async def admin_rooms(self, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        return _ok(await self.raw_get("/api/admin/rooms", params=self._admin_params(),
                                      server=server, timeout=8), "房间列表")

    async def admin_kick(self, player_id: str, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        if not player_id:
            raise NetProtocolError("缺少 playerId")
        return _ok(await self.raw_get("/api/admin/kick",
                                      params=self._admin_params(playerId=player_id),
                                      server=server, timeout=8), "踢人")

    async def admin_kickall(self, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        return _ok(await self.raw_get("/api/admin/kickall", params=self._admin_params(),
                                      server=server, timeout=8), "全员踢出")

    async def admin_close_room(self, code: str, *, force: bool = False,
                               server: str = SERVER_MAIN) -> Mapping[str, Any]:
        code = (code or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{%d}" % ROOM_CODE_LEN, code):
            raise NetProtocolError("房间密钥必须是 %d 位字母或数字" % ROOM_CODE_LEN)
        params = self._admin_params(code=code, force="1" if force else None)
        return _ok(await self.raw_get("/api/admin/room/close", params=params,
                                      server=server, timeout=8), "关闭房间")

    async def admin_announce(self, title: str, content: str, *, force: bool = True,
                             start_at: Optional[int] = None, expire_at: Optional[int] = None,
                             server: str = SERVER_MAIN) -> Mapping[str, Any]:
        content = (content or "").strip()
        if not content:
            raise NetProtocolError("公告正文不能为空")
        params = self._admin_params(
            title=(title or "").strip() or None, content=content,
            force="true" if force else "false", startAt=start_at, expireAt=expire_at,
        )
        return _ok(await self.raw_get("/api/admin/announce", params=params,
                                      server=server, timeout=8), "发布公告")

    async def admin_announce_clear(self, server: str = SERVER_MAIN) -> Mapping[str, Any]:
        return _ok(await self.raw_get("/api/admin/announce-clear", params=self._admin_params(),
                                      server=server, timeout=8), "撤回公告")

    async def admin_restart(self, reason: str = "bot", server: str = SERVER_MAIN) -> Mapping[str, Any]:
        return _ok(await self.raw_get("/api/admin/restart",
                                      params=self._admin_params(reason=(reason or "bot")[:120]),
                                      server=server, timeout=8), "重启服务")


class LocalStatusApi:
    """宿主机只读状态代理（stronghold-control，token 由代理端持有）。"""

    def __init__(self, transport: HttpTransport, base: str = DEFAULT_LOCAL_BASE):
        self.transport = transport
        self.base = (base or DEFAULT_LOCAL_BASE).rstrip("/")

    async def stability(self, port: int, *, timeout: float = 6.0) -> Mapping[str, Any]:
        data = await self.transport.request(
            "GET", self.base + "/stability", params={"port": int(port)},
            timeout=timeout, retry=FAST_RETRY,
        )
        return _as_mapping(data, "本地状态", self.base + "/stability")


# --------------------------------------------------------------------------- WebSocket


def ws_url_from(base: str) -> str:
    base = (base or DEFAULT_MAIN_BASE).rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + "/ws"


def validate_outgoing(t: str, fields: Mapping[str, Any]) -> None:
    """按 shared/protocol.js 的 C2S 规则做本地预校验，避免把坏帧发出去。"""
    if t == "hello":
        name = fields.get("name")
        if not isinstance(name, str) or not (0 < len(name.strip()) <= NAME_MAX_LEN):
            raise NetProtocolError("hello.name 必须是 1—%d 个字符" % NAME_MAX_LEN)
        token = fields.get("token")
        if token is not None and (not isinstance(token, str) or len(token) > 64):
            raise NetProtocolError("hello.token 最多 64 个字符")
        return
    if t == "ping":
        if not isinstance(fields.get("c"), (int, float)):
            raise NetProtocolError("ping.c 必须是数字")
        return
    if t in ("room.join", "room.spectate"):
        code = fields.get("code")
        if not isinstance(code, str) or not ROOM_CODE_RE.fullmatch(code):
            raise NetProtocolError("房间密钥格式无效")
        return
    if t == "room.create":
        if fields.get("mode") not in ("solo", "coop"):
            raise NetProtocolError("room.create.mode 只能是 solo 或 coop")
        difficulty = fields.get("difficulty")
        if difficulty is not None and difficulty not in DIFFICULTIES:
            raise NetProtocolError("难度必须是 %s" % "/".join(sorted(DIFFICULTIES)))
        return
    if t == "room.ready":
        if not isinstance(fields.get("ready"), bool):
            raise NetProtocolError("room.ready.ready 必须是布尔值")
        return
    if t == "b.progress":
        for key in ("battleId", "gt", "killed", "total"):
            if key not in fields:
                raise NetProtocolError("b.progress 缺少 %s" % key)
        return
    if t == "b.result":
        if "battleId" not in fields or not isinstance(fields.get("result"), Mapping):
            raise NetProtocolError("b.result 需要 battleId 与 result")
        return


class GameSocketClient:
    """一条 /ws 连接：握手、心跳、rid 应答、事件分发、重连。"""

    NO_RECONNECT_CODES = frozenset({4001, 4002, 1008, 1001})

    def __init__(self, url: str, *, name: str = "大肥鱼", token: Optional[str] = None,
                 version: Optional[int] = None, delta: Optional[int] = None,
                 heartbeat: float = 20.0, connect_timeout: float = 8.0,
                 ack_timeout: float = 8.0, auto_reconnect: bool = False,
                 max_reconnect_delay: float = 30.0, logger: Optional[Any] = None,
                 session_factory: Optional[Callable[[], Any]] = None):
        self.url = url
        self.name = name
        self.token = token
        self.version = version
        self.delta = delta
        self.heartbeat = float(heartbeat)
        self.connect_timeout = float(connect_timeout)
        self.ack_timeout = float(ack_timeout)
        self.auto_reconnect = bool(auto_reconnect)
        self.max_reconnect_delay = float(max_reconnect_delay)
        self.logger = logger
        self._session_factory = session_factory
        self._session = None
        self._ws = None
        self._reader_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._rid = itertools.count(1)
        self._closed = False
        self._closing_code: Optional[int] = None
        self._welcome: Optional[Mapping[str, Any]] = None
        self.last_pong_at: float = 0.0
        self.latency_ms: Optional[float] = None

    # ---- 生命周期 ----

    async def _get_session(self):
        if self._session is None or getattr(self._session, "closed", False):
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                self._session = aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=self.connect_timeout + 5),
                )
        return self._session

    async def connect(self) -> Mapping[str, Any]:
        if self._closed:
            raise NetUnavailable("WebSocket 已关闭")
        if self.connected:
            return self._welcome or {}
        session = await self._get_session()
        try:
            ws = await session.ws_connect(self.url, timeout=self.connect_timeout,
                                          heartbeat=None, autoping=True,
                                          max_msg_size=4 << 20)
        except asyncio.TimeoutError:
            raise NetTimeout("连接游戏服务器 WebSocket 超时")
        except (aiohttp.ClientError, OSError) as exc:
            raise NetUnavailable("无法连接游戏服务器 WebSocket：%s" % (str(exc)[:120]
                                                                  or type(exc).__name__))
        self._ws = ws
        self._reader_task = asyncio.create_task(self._read_loop())
        hello = {"name": self.name}
        if self.token:
            hello["token"] = self.token
        if self.version is not None:
            hello["version"] = self.version
        if self.delta is not None:
            hello["delta"] = self.delta
        try:
            welcome = await self._request("hello", hello, timeout=self.connect_timeout)
        except Exception:
            await self.close(1000)
            raise
        self._welcome = welcome
        if self.heartbeat > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self.logger:
            self.logger.debug("[wushu_net] ws 已连接 %s" % self.url)
        return welcome

    @property
    def connected(self) -> bool:
        ws = self._ws
        return ws is not None and not getattr(ws, "closed", False) and not self._closed

    async def close(self, code: int = 1000) -> None:
        self._closed = True
        self._closing_code = code
        for task in (self._heartbeat_task, self._reader_task):
            if task is not None and not task.done():
                task.cancel()
        self._heartbeat_task = self._reader_task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close(code=code)
            except Exception:
                pass
        self._fail_pending(SocketError("WebSocket 已关闭"))
        session, self._session = self._session, None
        if session is not None and not getattr(session, "closed", False):
            try:
                await session.close()
            except Exception:
                pass

    # ---- 发送 / 应答 ----

    async def send(self, t: str, *, rid: Any = None, timeout: Optional[float] = None,
                   **fields: Any) -> Mapping[str, Any]:
        if not self.connected:
            raise NetUnavailable("WebSocket 未连接")
        validate_outgoing(t, fields)
        return await self._request(t, fields, rid=rid, timeout=timeout)

    async def _request(self, t: str, fields: Mapping[str, Any], *, rid: Any = None,
                       timeout: Optional[float] = None) -> Mapping[str, Any]:
        request_id = rid if rid is not None else next(self._rid)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        payload = {"t": t, "rid": request_id, **fields}
        try:
            await self._ws.send_str(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout or self.ack_timeout)
        except asyncio.TimeoutError:
            future.cancel()
            raise NetTimeout("等待服务器应答超时（%s）" % t)
        finally:
            self._pending.pop(request_id, None)

    def on(self, event_type: str, handler: Callable[..., Any]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def wait_for(self, event_type: str, *, timeout: float = 10.0) -> Mapping[str, Any]:
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def once(frame):
            if not future.done():
                future.set_result(frame)

        self.on(event_type, once)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            handlers = self._handlers.get(event_type, [])
            if once in handlers:
                handlers.remove(once)
            if not handlers:
                self._handlers.pop(event_type, None)

    async def _read_loop(self) -> None:
        ws = self._ws
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        frame = json.loads(message.data)
                    except json.JSONDecodeError:
                        if self.logger:
                            self.logger.warning("[wushu_net] 收到非 JSON WebSocket 帧")
                        continue
                    if isinstance(frame, Mapping):
                        self._handle_frame(frame)
                elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 连接中断
            if self.logger:
                self.logger.debug("[wushu_net] ws 读取结束：%s" % str(exc)[:100])
        finally:
            code = getattr(ws, "close_code", None)
            self._closing_code = code if isinstance(code, int) else self._closing_code
            self._fail_pending(SocketError("WebSocket 连接已断开"))
            if not self._closed and self.auto_reconnect and self._closing_code not in self.NO_RECONNECT_CODES:
                asyncio.create_task(self._reconnect())

    def _handle_frame(self, frame: Mapping[str, Any]) -> None:
        t = frame.get("t")
        rid = frame.get("rid")
        if t == "welcome":
            self._welcome = frame
        if rid is not None and rid in self._pending:
            future = self._pending[rid]
            if not future.done():
                if t == "error":
                    future.set_exception(SocketError(
                        "服务器拒绝请求：%s" % (frame.get("error") or "未知错误"),
                        error_code=str(frame.get("error") or ""),
                        rid=rid, detail=str(frame.get("detail") or ""),
                    ))
                else:
                    future.set_result(frame)
        if t == "pong":
            self.last_pong_at = time.monotonic()
            sent = frame.get("c")
            if isinstance(sent, (int, float)):
                self.latency_ms = max(0, int(time.monotonic() * 1000 - sent))
        for handler in list(self._handlers.get(str(t), [])) + list(self._handlers.get("*", [])):
            try:
                result = handler(frame)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as exc:
                if self.logger:
                    self.logger.warning("[wushu_net] 事件处理器失败：%s" % str(exc)[:100])

    async def _heartbeat_loop(self) -> None:
        try:
            while self.connected:
                await asyncio.sleep(self.heartbeat)
                if not self.connected:
                    break
                try:
                    await self._request("ping", {"c": int(time.monotonic() * 1000)},
                                        timeout=min(self.heartbeat, self.ack_timeout))
                except NetError:
                    if self.last_pong_at and time.monotonic() - self.last_pong_at > self.heartbeat * 3:
                        await self.close(1001)
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.logger:
                self.logger.debug("[wushu_net] 心跳结束：%s" % str(exc)[:100])

    async def _reconnect(self) -> None:
        delay = 1.0
        while not self._closed:
            try:
                if self.logger:
                    self.logger.info("[wushu_net] %.0f 秒后重连 WebSocket" % delay)
                await asyncio.sleep(delay)
                if self._closed:
                    return
                await self.connect()
                return
            except Exception as exc:
                if self.logger:
                    self.logger.debug("[wushu_net] 重连失败：%s" % str(exc)[:100])
                delay = min(self.max_reconnect_delay, delay * 2)

    def _fail_pending(self, error: NetError) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    # ---- 常用指令 ----

    async def create_room(self, mode: str = "solo", difficulty: Optional[str] = None,
                          **extra: Any) -> Mapping[str, Any]:
        fields = {"mode": mode, **({"difficulty": difficulty} if difficulty else {}), **extra}
        return await self.send("room.create", **fields)

    async def join_room(self, code: str) -> Mapping[str, Any]:
        return await self.send("room.join", code=code)

    async def spectate(self, code: str) -> Mapping[str, Any]:
        return await self.send("room.spectate", code=code)

    async def leave_room(self) -> Mapping[str, Any]:
        return await self.send("room.leave")

    async def ready(self, ready: bool = True) -> Mapping[str, Any]:
        return await self.send("room.ready", ready=bool(ready))

    async def report_progress(self, **fields: Any) -> Mapping[str, Any]:
        return await self.send("b.progress", **fields)

    async def report_result(self, battle_id: Any, result: Mapping[str, Any],
                            cs: Optional[str] = None) -> Mapping[str, Any]:
        fields = {"battleId": battle_id, "result": dict(result)}
        if cs:
            fields["cs"] = cs
        return await self.send("b.result", **fields)


# --------------------------------------------------------------------------- 组合入口


class NetworkManager:
    """插件只依赖这一个对象：HTTP API、本地状态、按需 WebSocket。"""

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *,
                 transport: Optional[HttpTransport] = None):
        cfg = config or {}
        timeout = _num(cfg.get("request_timeout_seconds"), 8.0)
        attempts = int(_num(cfg.get("request_retries"), 3))
        self.transport = transport or HttpTransport()
        # 注入的传输层也要跟着配置走，否则测试与线上行为不一致
        self.transport.timeout = timeout
        self.transport.retry = RetryPolicy(attempts=max(1, attempts))
        self.api = GameApi(
            self.transport,
            str(cfg.get("main_base") or DEFAULT_MAIN_BASE),
            str(cfg.get("alliance_base") or DEFAULT_ALLIANCE_BASE),
            str(cfg.get("admin_token") or ""),
        )
        self.local = LocalStatusApi(self.transport,
                                    str(cfg.get("local_status_base") or DEFAULT_LOCAL_BASE))
        self.ws_url = str(cfg.get("ws_url") or ws_url_from(self.api.main_base))
        self.ws_name = str(cfg.get("ws_name") or "大肥鱼")
        try:  # 0 表示关闭心跳，不能走 _num（它会把 0 当无效值）
            self.ws_heartbeat = max(0.0, float(cfg.get("ws_heartbeat_seconds", 20)))
        except (TypeError, ValueError):
            self.ws_heartbeat = 20.0
        self._ws_token = str(cfg.get("ws_token") or "").strip() or None

    def remember_ws_token(self, token: Optional[str]) -> None:
        if token and len(str(token)) <= 64:
            self._ws_token = str(token)

    def socket(self, *, auto_reconnect: bool = False, name: Optional[str] = None,
               logger: Optional[Any] = None, token: Optional[str] = None) -> GameSocketClient:
        return GameSocketClient(
            self.ws_url, name=name or self.ws_name, token=token or self._ws_token,
            version=PROTOCOL_VERSION, heartbeat=self.ws_heartbeat,
            auto_reconnect=auto_reconnect, logger=logger,
        )

    async def close(self) -> None:
        await self.transport.close()


def _num(value: Any, default: float) -> float:
    try:
        number = float(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default


__all__ = [
    "NetError", "NetTimeout", "NetUnavailable", "NetProtocolError", "NetHttpError",
    "NetAuthError", "NetNotFound", "NetConflict", "NetRateLimited", "SocketError",
    "RetryPolicy", "NO_RETRY", "FAST_RETRY", "HttpTransport", "RawResponse",
    "GameApi", "LocalStatusApi", "GameSocketClient", "NetworkManager",
    "DEFAULT_MAIN_BASE", "DEFAULT_ALLIANCE_BASE", "DEFAULT_LOCAL_BASE",
    "SERVER_MAIN", "SERVER_ALLIANCE", "SERVERS", "ws_url_from", "validate_outgoing",
    "PROTOCOL_VERSION",
]
