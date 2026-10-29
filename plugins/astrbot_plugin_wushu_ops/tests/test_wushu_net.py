"""wushu_net 单元测试（不访问真实网络）。

运行：python -m unittest discover -s tests -v
联调（真实服务器，需显式开启）：$env:WUSHU_LIVE=1; python -m unittest tests.test_wushu_net -v
"""

import asyncio
import json
import os
import sys
import types
import unittest

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wushu_net as wn  # noqa: E402


class ScriptedTransport(wn.HttpTransport):
    """按脚本返回响应，记录实际发出的请求参数。"""

    def __init__(self, script, **kwargs):
        super().__init__(**kwargs)
        self.script = list(script)
        self.calls = []

    async def _perform(self, method, url, *, params, json_body, headers, timeout):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}),
                           "timeout": timeout})
        item = self.script.pop(0) if self.script else (500, {}, b"{}")
        if isinstance(item, BaseException):
            raise item
        status, headers_, body = item
        return wn.RawResponse(status=status, headers=headers_, body=body, url=url)


def _json(status, payload, headers=None):
    return (status, headers or {}, json.dumps(payload).encode("utf-8"))


def _ok_body(**extra):
    return _json(200, {"ok": True, **extra})


class HttpTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds(self):
        transport = ScriptedTransport(
            [_json(503, {"error": "busy"}), _json(503, {}), _ok_body(value=7)],
            retry=wn.RetryPolicy(attempts=3, base_delay=0.001, max_delay=0.002, jitter=0),
        )
        data = await transport.request("GET", "https://example.test/x")
        self.assertEqual(data["value"], 7)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(transport.stats["retries"], 2)
        await transport.close()

    async def test_timeout_raises_net_timeout(self):
        transport = ScriptedTransport(
            [asyncio.TimeoutError()],
            retry=wn.RetryPolicy(attempts=1, base_delay=0.001, jitter=0),
        )
        with self.assertRaises(wn.NetTimeout):
            await transport.request("GET", "https://example.test/x", timeout=0.5)
        self.assertEqual(transport.stats["failures"], 1)
        await transport.close()

    async def test_status_mapping(self):
        cases = [
            (403, wn.NetAuthError),
            (401, wn.NetAuthError),
            (404, wn.NetNotFound),
            (409, wn.NetConflict),
            (429, wn.NetRateLimited),
            (418, wn.NetHttpError),
        ]
        for status, expected in cases:
            transport = ScriptedTransport([_json(status, {"error": "x"})], retry=wn.NO_RETRY)
            with self.assertRaises(expected):
                await transport.request("GET", "https://example.test/x")
            await transport.close()

    async def test_invalid_json_is_protocol_error(self):
        transport = ScriptedTransport([(200, {}, b"<html>nope</html>")])
        with self.assertRaises(wn.NetProtocolError):
            await transport.request("GET", "https://example.test/x")
        await transport.close()

    async def test_retry_after_header(self):
        transport = ScriptedTransport(
            [_json(429, {}, {"retry-after": "0"}), _ok_body()],
            retry=wn.RetryPolicy(attempts=2, base_delay=0.001, jitter=0),
        )
        self.assertTrue((await transport.request("GET", "https://example.test/x"))["ok"])
        self.assertEqual(transport.stats["retries"], 1)
        await transport.close()

    async def test_non_idempotent_not_retried(self):
        transport = ScriptedTransport([_json(503, {}), _ok_body()])
        with self.assertRaises(wn.NetHttpError):
            await transport.request("POST", "https://example.test/x")
        self.assertEqual(len(transport.calls), 1)
        await transport.close()


class GameApiTest(unittest.IsolatedAsyncioTestCase):
    def make(self, script, token="secret"):
        transport = ScriptedTransport(script, retry=wn.NO_RETRY)
        api = wn.GameApi(transport, "https://main.test", "https://alliance.test", token)
        return transport, api

    async def test_url_and_server_routing(self):
        _, api = self.make([])
        self.assertEqual(api.url("/healthz"), "https://main.test/healthz")
        self.assertEqual(api.url("api/online", wn.SERVER_ALLIANCE), "https://alliance.test/api/online")

    async def test_health_and_online(self):
        health_body = _ok_body(app="0.3.1", rooms=3)
        transport, api = self.make([health_body, health_body,
                                    _json(200, {"online": 5, "humans": 2})])
        health = await api.health()
        self.assertEqual(health["app"], "0.3.1")
        self.assertTrue(await api.health_ok())
        online = await api.online()
        self.assertEqual(online["humans"], 2)
        self.assertEqual(transport.calls[0]["url"], "https://main.test/healthz")

    async def test_admin_requires_token(self):
        transport, api = self.make([], token="")
        with self.assertRaises(wn.NetAuthError):
            await api.admin_players()
        self.assertEqual(transport.calls, [])

    async def test_admin_kick_params(self):
        transport, api = self.make([_ok_body(kicked="p1", name="鱼", banSec=60)])
        data = await api.admin_kick("p1")
        self.assertEqual(data["kicked"], "p1")
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://main.test/api/admin/kick")
        self.assertEqual(call["params"], {"playerId": "p1", "token": "secret"})

    async def test_close_room_validation_and_force(self):
        transport, api = self.make([_ok_body(code="ABCD", kicked=1)])
        with self.assertRaises(wn.NetProtocolError):
            await api.admin_close_room("ABC")
        data = await api.admin_close_room("abcd", force=True)
        self.assertTrue(data["closed"] if "closed" in data else data["ok"])
        params = transport.calls[0]["params"]
        self.assertEqual(params["code"], "ABCD")
        self.assertEqual(params["force"], "1")

    async def test_announce_params(self):
        transport, api = self.make([_ok_body(forceId="a1")])
        await api.admin_announce("更新", "今晚 22 点维护", force=False, start_at=1000, expire_at=2000)
        params = transport.calls[0]["params"]
        self.assertEqual(params["title"], "更新")
        self.assertEqual(params["content"], "今晚 22 点维护")
        self.assertEqual(params["force"], "false")
        self.assertEqual(params["startAt"], 1000)
        self.assertEqual(params["expireAt"], 2000)
        with self.assertRaises(wn.NetProtocolError):
            await api.admin_announce("t", "  ")

    async def test_daily_challenges_division(self):
        transport, api = self.make([_ok_body(day="2026-10-28", challenges=[])])
        await api.daily_challenges("solo", ranking=True)
        self.assertEqual(transport.calls[0]["params"], {"division": "solo", "ranking": "1"})
        with self.assertRaises(wn.NetProtocolError):
            await api.daily_challenges("duo")

    async def test_ok_false_is_protocol_error(self):
        _, api = self.make([_json(200, {"ok": False})])
        with self.assertRaises(wn.NetProtocolError):
            await api.admin_rooms()

    async def test_local_stability(self):
        transport = ScriptedTransport([_json(200, {"ok": True, "active": True})])
        local = wn.LocalStatusApi(transport, "http://local.test")
        data = await local.stability(3000)
        self.assertTrue(data["active"])
        self.assertEqual(transport.calls[0]["params"], {"port": 3000})


class ValidateTest(unittest.TestCase):
    def test_valid_frames(self):
        wn.validate_outgoing("hello", {"name": "大肥鱼"})
        wn.validate_outgoing("ping", {"c": 1})
        wn.validate_outgoing("room.join", {"code": "ABCD"})
        wn.validate_outgoing("room.create", {"mode": "solo", "difficulty": "ABYSS"})
        wn.validate_outgoing("room.ready", {"ready": True})
        wn.validate_outgoing("b.progress", {"battleId": 1, "gt": 1, "killed": 0, "total": 10})

    def test_invalid_frames(self):
        bad = [
            ("hello", {"name": ""}),
            ("hello", {"name": "x" * 13}),
            ("ping", {"c": "now"}),
            ("room.join", {"code": "!"}),
            ("room.create", {"mode": "duo"}),
            ("room.create", {"mode": "solo", "difficulty": "EASY"}),
            ("room.ready", {"ready": 1}),
            ("b.progress", {"battleId": 1}),
            ("b.result", {"battleId": 1}),
        ]
        for t, fields in bad:
            with self.assertRaises(wn.NetProtocolError, msg=t):
                wn.validate_outgoing(t, fields)

    def test_ws_url(self):
        self.assertEqual(wn.ws_url_from("https://game.test"), "wss://game.test/ws")
        self.assertEqual(wn.ws_url_from("http://1.2.3.4:3000/"), "ws://1.2.3.4:3000/ws")


class _Message:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class FakeWebSocket:
    def __init__(self, responder):
        self.responder = responder
        self.sent = []
        self.closed = False
        self.close_code = None
        self._queue = asyncio.Queue()

    async def send_str(self, data):
        payload = json.loads(data)
        self.sent.append(payload)
        frame = self.responder(payload)
        if frame is not None:
            await self._queue.put(_Message(aiohttp.WSMsgType.TEXT, json.dumps(frame)))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self, code=1000):
        self.closed = True
        self.close_code = code
        await self._queue.put(None)


class FakeSession:
    def __init__(self, ws):
        self.ws = ws
        self.closed = False

    async def ws_connect(self, url, **kwargs):
        return self.ws

    async def close(self):
        self.closed = True


def _responder(payload):
    t = payload.get("t")
    rid = payload.get("rid")
    if t == "hello":
        return {"t": "welcome", "rid": rid, "playerId": "p1", "name": payload.get("name"),
                "token": "tok-1", "version": 1, "resumed": False}
    if t == "ping":
        return {"t": "pong", "rid": rid, "c": payload.get("c")}
    if t == "room.join":
        if payload.get("code") == "NOPE":
            return {"t": "error", "rid": rid, "error": "ROOM_NOT_FOUND"}
        return {"t": "ok", "rid": rid}
    return {"t": "ok", "rid": rid}


class SocketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = FakeWebSocket(_responder)
        self.session = FakeSession(self.ws)

    def make(self, **kwargs):
        return wn.GameSocketClient(
            "wss://game.test/ws", name="大肥鱼", heartbeat=0, auto_reconnect=False,
            session_factory=lambda: self.session, **kwargs,
        )

    async def test_handshake_ping_and_error(self):
        client = self.make()
        welcome = await client.connect()
        self.assertEqual(welcome["token"], "tok-1")
        self.assertEqual(self.ws.sent[0]["t"], "hello")
        await client.send("ping", c=int(__import__("time").monotonic() * 1000))
        self.assertIsNotNone(client.latency_ms)
        self.assertGreaterEqual(client.latency_ms, 0)
        await client.join_room("ABCD")
        with self.assertRaises(wn.SocketError) as ctx:
            await client.join_room("NOPE")
        self.assertEqual(ctx.exception.error_code, "ROOM_NOT_FOUND")
        await client.close(1000)
        self.assertTrue(self.ws.closed)

    async def test_send_requires_connection(self):
        client = self.make()
        with self.assertRaises(wn.NetUnavailable):
            await client.send("ping", c=1)

    async def test_ack_timeout(self):
        self.ws.responder = lambda payload: None  # 只发不收
        client = self.make(ack_timeout=0.05, connect_timeout=0.05)
        with self.assertRaises(wn.NetTimeout):
            await client.connect()  # hello 等不到 welcome
        self.assertTrue(self.ws.closed)


class ManagerTest(unittest.TestCase):
    def test_config_addresses_and_defaults(self):
        manager = wn.NetworkManager({
            "main_base": "https://m.test/",
            "alliance_base": "https://a.test/",
            "admin_token": "tk",
            "ws_token": "wst",
            "local_status_base": "http://local.test/",
            "request_timeout_seconds": 5,
            "request_retries": 2,
            "ws_name": "测试鱼",
            "ws_heartbeat_seconds": 0,
        }, transport=wn.HttpTransport())
        self.assertEqual(manager.api.url("/healthz"), "https://m.test/healthz")
        self.assertEqual(manager.api.url("/healthz", wn.SERVER_ALLIANCE), "https://a.test/healthz")
        self.assertEqual(manager.local.base, "http://local.test")
        self.assertTrue(manager.api.admin_ready())
        self.assertEqual(manager.transport.timeout, 5.0)
        self.assertEqual(manager.transport.retry.attempts, 2)
        self.assertEqual(manager.ws_url, "wss://m.test/ws")
        self.assertEqual(manager.ws_heartbeat, 0.0)
        sock = manager.socket()
        self.assertEqual(sock.name, "测试鱼")
        self.assertEqual(sock.token, "wst")

    def test_ws_token_remember(self):
        manager = wn.NetworkManager({"admin_token": "tk"}, transport=wn.HttpTransport())
        manager.remember_ws_token("tok-9")
        self.assertEqual(manager.socket().token, "tok-9")
        manager.remember_ws_token("x" * 100)
        self.assertEqual(manager.socket().token, "tok-9")


LIVE = os.environ.get("WUSHU_LIVE") == "1"


@unittest.skipUnless(LIVE, "设置 WUSHU_LIVE=1 才运行真实服务器联调")
class LiveTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_http_and_ws(self):
        manager = wn.NetworkManager({})
        try:
            health = await manager.api.health(wn.SERVER_MAIN, timeout=10)
            self.assertTrue(health.get("app"))
            online = await manager.api.online(wn.SERVER_MAIN, timeout=10)
            self.assertIn("online", online)
            await manager.api.announcements(wn.SERVER_MAIN, timeout=10)
            daily = await manager.api.daily_challenges("solo", ranking=True, timeout=10)
            self.assertIn("rows", daily)
            sock = manager.socket()
            welcome = await sock.connect()
            self.assertTrue(welcome.get("playerId"))
            await sock.send("ping", c=int(__import__("time").monotonic() * 1000))
            self.assertIsNotNone(sock.latency_ms)
            await sock.close(1000)
        finally:
            await manager.close()


if __name__ == "__main__":
    unittest.main()
