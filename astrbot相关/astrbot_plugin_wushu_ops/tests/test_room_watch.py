"""等待房间播报单元测试（不访问真实网络）。

运行：python -m unittest tests.test_room_watch -v
"""

import asyncio
import importlib
import os
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 插件目录名带连字符，不能直接当包导入；这里造一个包外壳来加载相对导入
if "wushu_review" not in sys.modules:
    package = types.ModuleType("wushu_review")
    package.__path__ = [ROOT]
    sys.modules["wushu_review"] = package

rw = importlib.import_module("wushu_review.room_watch")
wn = importlib.import_module("wushu_review.wushu_net")

SERVER = wn.SERVER_ALLIANCE


def room(code="A1B2", humans=1, connected=None, mode="coop", in_match=False,
         host="h1", difficulty="NORMAL"):
    return {
        "code": code, "mode": mode, "difficulty": difficulty,
        "inMatch": in_match, "humanCount": humans,
        "connectedHumans": humans if connected is None else connected,
        "hostId": host, "audience": "live",
    }


class FakeApi:
    def __init__(self, rooms=None, token="test-token"):
        self.rooms = rooms or {}
        self.admin_token = token
        self.calls = []
        self.fail = set()
        self.players = [{"playerId": "h1", "name": "房主甲"}]

    def admin_ready(self):
        return bool(self.admin_token)

    async def admin_rooms(self, server=SERVER):
        self.calls.append(server)
        if server in self.fail:
            raise wn.NetUnavailable("模拟断线")
        rooms = self.rooms.get(server, [])
        return {"ok": True, "count": len(rooms), "rooms": rooms}

    async def admin_players(self, server=SERVER):
        if server in self.fail:
            raise wn.NetUnavailable("模拟断线")
        return {"ok": True, "count": len(self.players), "players": self.players}


class FakePlugin:
    def __init__(self, api, config=None):
        self.net = types.SimpleNamespace(api=api)
        self.config = config or {}
        self.sent = []

    async def _broadcast(self, text, targets=None):
        self.sent.append(text)
        self.targets = targets
        return (targets or ["fake:GroupMessage:1"], [])


def runtime_for(api, config=None):
    config = {"room_watch_countdown_seconds": 0.01,
              "room_watch_servers": SERVER,
              **(config or {})}
    return types.SimpleNamespace(plugin=FakePlugin(api, config))


class RoomWatchTest(unittest.IsolatedAsyncioTestCase):
    async def finish(self, watch):
        """等所有倒计时任务跑完（复核与播报都是本地 fake，立即返回）。"""
        deadline = time.time() + 1.0
        while time.time() < deadline:
            pending = [t for t in list(watch._countdowns.values()) if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_waiting_rule(self):
        watch = rw.RoomWatch(runtime_for(FakeApi()))
        self.assertTrue(watch.waiting(room(humans=1)))
        self.assertTrue(watch.waiting(room(humans=5)))
        self.assertFalse(watch.waiting(room(humans=6)))
        self.assertFalse(watch.waiting(room(humans=1, mode="solo")))
        self.assertFalse(watch.waiting(room(humans=1, in_match=True)))
        self.assertFalse(watch.waiting(room(humans=1, connected=0)))
        self.assertFalse(watch.waiting({"code": ""}))
        self.assertFalse(watch.waiting(None))

    async def test_countdown_then_announce_once(self):
        api = FakeApi({SERVER: [room()]})
        watch = rw.RoomWatch(runtime_for(
            api, {"room_watch_targets": "snowluma:GroupMessage:1104628970"}))
        await watch.scan()
        await self.finish(watch)
        sent = watch.r.plugin.sent
        self.assertEqual(watch.r.plugin.targets, ["snowluma:GroupMessage:1104628970"])
        self.assertEqual(len(sent), 1)
        self.assertIn("A1B2", sent[0])
        self.assertIn("1/6", sent[0])
        self.assertIn("险境", sent[0])
        self.assertIn("房主甲在等", sent[0])
        # 房号只出现一次，避免重复刷屏
        self.assertEqual(sent[0].count("A1B2"), 1)
        # 同一房间持续等待时不重复刷屏
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(len(watch.r.plugin.sent), 1)

    async def test_countdown_cancelled_when_room_fills(self):
        api = FakeApi({SERVER: [room()]})
        watch = rw.RoomWatch(runtime_for(api, {"room_watch_countdown_seconds": 0.2}))
        await watch.scan()
        self.assertIn(SERVER + ":A1B2", watch._countdowns)
        api.rooms[SERVER] = [room(humans=6)]
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(watch.r.plugin.sent, [])
        self.assertEqual(watch._tracks, {})

    async def test_reannounce_after_room_empties_again(self):
        api = FakeApi({SERVER: [room()]})
        watch = rw.RoomWatch(runtime_for(api))
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(len(watch.r.plugin.sent), 1)
        api.rooms[SERVER] = []
        await watch.scan()
        api.rooms[SERVER] = [room()]
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(len(watch.r.plugin.sent), 2)

    async def test_api_failure_is_logged_and_recovers(self):
        api = FakeApi({SERVER: []}, token="")
        watch = rw.RoomWatch(runtime_for(api))
        await watch.scan()
        self.assertEqual(api.calls, [])  # 无令牌时不发请求
        self.assertIn("admin_token", await watch.describe())

        api.admin_token = "test-token"
        api.fail.add(SERVER)
        await watch.scan()  # 不应抛出
        self.assertEqual(watch.r.plugin.sent, [])
        api.fail.discard(SERVER)
        api.rooms[SERVER] = [room()]
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(len(watch.r.plugin.sent), 1)

    async def test_host_away_cancels_announcement(self):
        api = FakeApi({SERVER: [room()]})
        api.players = []  # 房主已掉线，不在在线玩家列表里
        watch = rw.RoomWatch(runtime_for(api))
        await watch.scan()
        await self.finish(watch)
        self.assertEqual(watch.r.plugin.sent, [])
        self.assertEqual(watch._tracks, {})

    async def test_describe_marks_pending(self):
        api = FakeApi({SERVER: [room(), room(code="C3D4", humans=6)]})
        watch = rw.RoomWatch(runtime_for(api, {"room_watch_countdown_seconds": 30}))
        await watch.scan()
        text = await watch.describe()
        self.assertIn("A1B2", text)
        self.assertNotIn("C3D4", text)
        self.assertIn("倒计时中", text)
        await watch.stop()


if __name__ == "__main__":
    unittest.main()
