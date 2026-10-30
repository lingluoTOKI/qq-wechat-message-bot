"""好感度自动打分：纯函数与数据层测试。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "astrbot_plugin_dafeiyu_daily_memory"
sys.path.insert(0, str(PLUGIN_DIR))

import affinity_watch  # noqa: E402
from store import Store  # noqa: E402


class ParseScoreTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(affinity_watch.parse_score('{"score": 73, "reason": "常互动"}'), 73)
        self.assertEqual(affinity_watch.parse_score('```json\n{"score": 0}\n```'), 0)
        self.assertEqual(affinity_watch.parse_score('{"score": 100}'), 100)
        self.assertEqual(affinity_watch.parse_score('{"score": 73.0}'), 73)

    def test_invalid(self):
        for text in ("", "不是 JSON", '{"score": 101}', '{"score": -1}',
                     '{"score": 73.5}', '{"score": "73"}', '{"score": true}',
                     '{"score": null}', "截断 {", None):
            self.assertIsNone(affinity_watch.parse_score(text), text)


class SmoothTests(unittest.TestCase):
    def test_smooth(self):
        auto50 = {"score": 50, "source": "auto"}
        admin50 = {"score": 50, "source": "admin_set"}
        self.assertEqual(affinity_watch.smooth_score(80, None, 10), 80)          # 首次不钳制
        self.assertEqual(affinity_watch.smooth_score(80, auto50, 10), 60)        # 最多 +10
        self.assertEqual(affinity_watch.smooth_score(20, auto50, 10), 40)        # 最多 -10
        self.assertEqual(affinity_watch.smooth_score(55, auto50, 10), 55)        # 区间内不变
        self.assertEqual(affinity_watch.smooth_score(90, admin50, 10), 90)       # 上次是管理员分
        self.assertEqual(affinity_watch.smooth_score(80, auto50, 0), 80)         # 0 = 不限制
        self.assertEqual(affinity_watch.smooth_score(100, {"score": 95, "source": "auto"}, 10), 100)
        self.assertEqual(affinity_watch.smooth_score(-5, auto50, 10), 40)


class LockTests(unittest.TestCase):
    def test_is_locked(self):
        self.assertFalse(affinity_watch.is_locked(None))
        self.assertTrue(affinity_watch.is_locked({"source": "admin_set"}))
        self.assertFalse(affinity_watch.is_locked({"source": "auto"}))
        self.assertFalse(affinity_watch.is_locked({"source": None}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "memory.db"))
        now = time.time()
        rows = [
            ("u1", "阿明", now - 60, 20, "爱开黑，说话短"),
            ("u2", "旧人", now - 30 * 86400, 50, "很久没说话"),
            ("u3", "新人", now - 3600, 2, "刚进群"),
            ("u4", "空画像", now - 60, 20, ""),
            ("u5", "无画像", now - 60, 20, None),
        ]
        for sid, name, last_seen, msg_count, summary in rows:
            self.store.db.execute(
                "INSERT INTO people(sender_id,name,aliases,first_seen,last_seen,msg_count,summary)"
                " VALUES(?,?,?,?,?,?,?)",
                (sid, name, "[]", last_seen, last_seen, msg_count, summary))
        self.store.db.commit()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_candidates_filter(self):
        rows = self.store.list_affinity_candidates(7, 15, 5)
        self.assertEqual([r["sender_id"] for r in rows], ["u1"])  # 旧人/新人/空画像/无画像都被排除
        self.assertEqual(self.store.list_affinity_candidates(40, 15, 5)[0]["sender_id"], "u1")
        self.assertEqual(len(self.store.list_affinity_candidates(40, 15, 5)), 2)
        self.assertEqual(self.store.list_affinity_candidates(7, 1, 5)[0]["sender_id"], "u1")

    def test_affinity_source(self):
        self.store.set_affinity("u1", 80)
        self.assertEqual(self.store.get_affinity_row("u1")["source"], "admin_set")
        self.store.set_affinity("u3", 55, "auto")
        row = self.store.get_affinity_row("u3")
        self.assertEqual((row["score"], row["source"]), (55, "auto"))
        self.assertIsNone(self.store.get_affinity_row("nope"))


if __name__ == "__main__":
    unittest.main()
