"""astrbot_plugin_dafeiyu_style 单元测试（不依赖 astrbot，可直接本机跑）。"""
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "astrbot_plugin_dafeiyu_style"
sys.path.insert(0, str(PLUGIN_DIR))

import inject  # noqa: E402
import learning  # noqa: E402
from store import Store  # noqa: E402

CFG = {
    "min_msg_len": 6, "max_msg_len": 300,
    "min_messages": 10, "max_batch": 100,
    "daily_rule_cap": 3, "total_rule_cap": 6,
    "inject_rules": 5, "inject_jargon": 5, "inject_shots": 3,
    "inject_max_chars": 600, "evidence_max_chars": 120,
}


def make_events(path: Path, count: int = 20, group: str = "g1") -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, group_id TEXT,"
        " sender_id TEXT, sender_name TEXT, content TEXT, is_self INTEGER, ts REAL)")
    for i in range(1, count + 1):
        conn.execute(
            "INSERT INTO events(id, session_id, group_id, sender_id, sender_name, content, is_self, ts)"
            " VALUES(?,?,?,?,?,?,0,?)",
            (i, "s1", group, "1001", "阿明", "这条消息有点内容 %s 号" % i, 1000 + i))
    conn.commit()
    conn.close()


def ask_payload(system, user, stage):
    if stage == "coarse":
        return _async('{"keep":[1,2,3,4,5]}')
    return _async(json.dumps({
        "rules": [{"rule": "短句、少主语", "evidence": "有点内容"},
                  {"rule": "句尾带语气词", "evidence": "内容 2 号"}],
        "jargon": [{"term": "开黑", "meaning": "一起打游戏", "confidence": 0.9}],
        "few_shots": [{"user_text": "上号吗", "bot_reply": "来，等我两分钟"}],
    }, ensure_ascii=False))


def _async(value):
    async def _inner():
        return value
    return _inner()


class ParseTests(unittest.TestCase):
    def test_payload_variants(self):
        good = json.dumps({"rules": [{"rule": "短句", "evidence": "原话"}],
                           "jargon": [{"term": "开黑", "meaning": "一起玩"}],
                           "few_shots": [{"user_text": "上号", "bot_reply": "来"}]},
                          ensure_ascii=False)
        self.assertEqual(len(learning.parse_payload(good)["rules"]), 1)
        self.assertIsNotNone(learning.parse_payload("```json\n%s\n```" % good))
        self.assertIsNone(learning.parse_payload("前面废话 {截断"))
        self.assertIsNone(learning.parse_payload("完全不是 JSON"))
        self.assertIsNone(learning.parse_payload(""))
        # 类型错误按空处理，不抛异常
        payload = learning.parse_payload('{"rules":"坏类型","jargon":null,"few_shots":[{"user_text":""}]}')
        self.assertEqual(payload, {"rules": [], "jargon": [], "few_shots": []})

    def test_keep_variants(self):
        self.assertEqual(learning.parse_keep('{"keep":[1,"2",3]}'), {1, 2, 3})
        self.assertIsNone(learning.parse_keep("坏的"))
        self.assertEqual(learning.parse_keep('{"keep":[]}'), set())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "style.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_caps_and_duplicates(self):
        self.store.add_rules("g1", [{"rule": "唯一", "evidence": "e"}])
        self.assertEqual(self.store.add_rules("g1", [{"rule": "唯一", "evidence": "e"}]), 0)
        items = [{"rule": "规则%d" % i, "evidence": "e"} for i in range(10)]
        self.assertEqual(self.store.add_rules("g1", items), 10)
        self.store.enforce_caps("g1", 3, 6)
        self.assertEqual(self.store.counts("g1")["style_rules"], 3)   # daily_rule_cap=3

    def test_state_machine(self):
        self.store.add_rules("g1", [{"rule": "短句", "evidence": "e"}])
        rid = self.store.pending("style_rules", "g1")[0]["id"]
        self.assertTrue(self.store.review("style_rules", rid, "approve", "admin"))
        self.assertEqual(len(self.store.approved("style_rules", "g1")), 1)
        # 已处理的记录不能再被改状态
        self.assertFalse(self.store.review("style_rules", rid, "reject", "admin"))
        self.assertFalse(self.store.review("style_rules", rid, "approve", "admin"))
        self.store.add_rules("g1", [{"rule": "另一条", "evidence": "e"}])
        rid2 = self.store.pending("style_rules", "g1")[0]["id"]
        self.store.review("style_rules", rid2, "reject", "admin")
        self.assertEqual(len(self.store.approved("style_rules", "g1")), 1)

    def test_all_approved_tables_query_without_missing_columns(self):
        for table in ('style_rules','jargon','few_shots'):
            self.assertEqual(self.store.approved(table,'g1'),[])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "memory.db"
        self.store = Store(str(self.dir / "style.db"))
        self.cfg = dict(CFG)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_missing_db_is_silent(self):
        rows, max_id = learning.read_events(str(self.dir / "nope.db"), 0, ["g1"], 6, 300, 100)
        self.assertEqual(rows, [])
        self.assertEqual(max_id, 0)
        self.assertEqual(learning.read_groups(str(self.dir / "nope.db")), [])

    def test_too_few_does_not_advance(self):
        make_events(self.db, 5)
        result = asyncio.run(learning.process_group(
            db_path=str(self.db), group_id="g1", since_id=0,
            cfg=self.cfg, ask=ask_payload, store=self.store))
        self.assertFalse(result["advanced"])
        self.assertEqual(result["error"], "too_few")
        self.assertEqual(self.store.get_meta("cursor:g1", "0"), "0")

    def test_nothing_to_learn_advances(self):
        make_events(self.db, 20)
        async def empty_keep(system, user, stage):
            return '{"keep":[]}' if stage == "coarse" else "{}"
        result = asyncio.run(learning.process_group(
            db_path=str(self.db), group_id="g1", since_id=0,
            cfg=self.cfg, ask=empty_keep, store=self.store))
        self.assertTrue(result["advanced"])
        self.assertEqual(result["error"], "nothing_to_learn")

    def test_parse_failure_does_not_advance(self):
        make_events(self.db, 20)
        async def bad(system, user, stage):
            return '{"keep":[1]}' if stage == "coarse" else "不是 JSON"
        result = asyncio.run(learning.process_group(
            db_path=str(self.db), group_id="g1", since_id=0,
            cfg=self.cfg, ask=bad, store=self.store))
        self.assertFalse(result["advanced"])
        self.assertEqual(result["error"], "parse")

    def test_budget_block_propagates_without_write(self):
        make_events(self.db, 20)
        async def blocked(system, user, stage):
            raise RuntimeError("辅助模型调用已暂停：预算已用完")
        with self.assertRaises(RuntimeError):
            asyncio.run(learning.process_group(
                db_path=str(self.db), group_id="g1", since_id=0,
                cfg=self.cfg, ask=blocked, store=self.store))
        self.assertEqual(self.store.get_meta("cursor:g1", "0"), "0")
        self.assertEqual(self.store.counts("g1"), {"style_rules": 0, "jargon": 0, "few_shots": 0})

    def test_happy_path(self):
        make_events(self.db, 20)
        result = asyncio.run(learning.process_group(
            db_path=str(self.db), group_id="g1", since_id=0,
            cfg=self.cfg, ask=ask_payload, store=self.store))
        self.assertTrue(result["advanced"])
        self.assertEqual(result["max_id"], 20)
        self.assertEqual(result["added"]["rules"], 2)
        self.assertEqual(result["added"]["jargon"], 1)
        self.assertEqual(result["added"]["few_shots"], 1)


class InjectTests(unittest.TestCase):
    def test_empty_and_bad_rows(self):
        self.assertEqual(inject.build_block(None, None, None), "")
        self.assertEqual(inject.build_block([], [], []), "")
        self.assertEqual(inject.build_block([{"rule": None}], [{}], [{"user_text": ""}]), "")

    def test_limits(self):
        rules = [{"rule": "很长的规则" + "啊" * 100} for _ in range(10)]
        block = inject.build_block(rules, [], [], max_chars=300)
        self.assertLessEqual(len(block), 300)
        self.assertIn("<style>", block)
        self.assertIn("不要复述", block)
        full = inject.build_block(rules, [{"term": "开黑", "meaning": "一起玩"}],
                                  [{"user_text": "上号", "bot_reply": "来"}], max_chars=600)
        self.assertIn("群内黑话", full)
        self.assertIn("参考对话", full)
        self.assertLessEqual(len(full), 600)


class RegressionTests(unittest.TestCase):
    def test_quota_zhipu_usage_keeps_gross_cost_and_rejects_other_models(self):
        from store import record_zhipu_budget
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/'cost.db');c=sqlite3.connect(path)
            c.execute('CREATE TABLE cost_supplements(umo TEXT,provider_id TEXT,provider_model TEXT,response_id TEXT,token_input_other INT,token_input_cached INT,token_output INT,injection_total INT,cost_amount REAL,currency_symbol TEXT,created_at TEXT)');c.close()
            raw={'id':'free-r','model':'glm-5.3-flashx','usage':{'prompt_tokens':20,'completion_tokens':10}}
            for _ in range(2):record_zhipu_budget(SimpleNamespace(raw_completion=raw),'qq:GroupMessage:g',path)
            c=sqlite3.connect(path);self.assertEqual(c.execute('SELECT count(*),sum(cost_amount),sum(token_output) FROM cost_supplements').fetchone(),(1,.00011,10));c.close()
            raw['model']='some-paid-model'
            with self.assertRaises(RuntimeError):record_zhipu_budget(SimpleNamespace(raw_completion=raw),'qq:GroupMessage:g',path)

    def test_debug_filter_keeps_normal_questions(self):
        import quality
        for text in ['/风格 跑一次','读取一下机器人日志','msg_seq报错','修改插件配置','Traceback error']:
            self.assertTrue(quality.exclusion(text),text)
        for text in ['小洛，你喜欢什么音乐？','今天想喝无糖乌龙茶','上号一起玩吗']:
            self.assertFalse(quality.exclusion(text),text)

    def test_auto_review_requires_two_speakers_and_rejects_debug(self):
        import quality
        item={'rule':'喜欢用短句','evidence':'今天一起上号'}
        rows=[{'id':1,'sender_id':'a','content':'今天一起上号吗'}, {'id':2,'sender_id':'b','content':'今天一起上号吧'}]
        self.assertEqual(quality.decision('style_rules',item,rows)[0],'approve')
        self.assertIsNone(quality.decision('style_rules',item,rows[:1])[0])
        self.assertEqual(quality.decision('style_rules',{'rule':'查询状态时直接抛英文参数','evidence':'参数'},[])[0],'reject')
        self.assertIsNone(quality.decision('jargon',{'term':'开黑','meaning':'一起玩'},rows)[0])

    def test_filtered_debug_never_calls_model_and_can_advance(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'memory.db';make_events(db,40)
            c=sqlite3.connect(db);c.execute("UPDATE events SET content='请读取机器人日志排查报错'");c.commit();c.close()
            s=Store(str(Path(td)/'style.db'))
            async def ask(*args):raise AssertionError('debug batch must not call model')
            r=asyncio.run(learning.process_group(db_path=str(db),group_id='g1',since_id=0,cfg=CFG,ask=ask,store=s))
            self.assertEqual(r['error'],'filtered_debug');self.assertTrue(r['advanced']);s.close()

    def test_budget_attribution_deduplicates_observed_response(self):
        from store import record_budget
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            ledger=str(Path(td)/'ledger.db');cost=str(Path(td)/'cost.db')
            c=sqlite3.connect(ledger);c.executescript("CREATE TABLE attempts(id TEXT,response_id TEXT);CREATE TABLE charges(attempt_id TEXT,started REAL,model TEXT,hit INT,miss INT,output INT,cost_nano INT);INSERT INTO attempts VALUES('a','r');INSERT INTO charges VALUES('a',1,'deepseek-flash',10,20,30,1000000);");c.close()
            c=sqlite3.connect(cost);c.execute('CREATE TABLE cost_supplements(umo TEXT,provider_id TEXT,provider_model TEXT,response_id TEXT,token_input_other INT,token_input_cached INT,token_output INT,injection_total INT,cost_amount REAL,currency_symbol TEXT,created_at TEXT)');c.close()
            for _ in range(2):record_budget(SimpleNamespace(id='r'),'qq:GroupMessage:g',ledger,cost)
            c=sqlite3.connect(cost);self.assertEqual(c.execute('SELECT count(*),sum(cost_amount) FROM cost_supplements').fetchone(),(1,.001));c.close()
            with self.assertRaises(RuntimeError):record_budget(SimpleNamespace(id='unknown'),'qq:GroupMessage:g',ledger,cost)

    def test_full_origin_matches_archive_with_different_group_label(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'memory.db';make_events(db,20,group='archive-label')
            con=sqlite3.connect(db)
            con.execute("UPDATE events SET session_id='qq:GroupMessage:actual-openid'")
            con.commit();con.close()
            origin='qq:GroupMessage:actual-openid'
            rows,last=learning.read_events(str(db),0,[origin],6,300,40)
            self.assertEqual(len(rows),20);self.assertEqual(last,20)
            self.assertEqual(learning.read_groups(str(db)),[origin])
            self.assertEqual(learning.read_events(str(db),0,['qq:GroupMessage:another'],6,300,40)[0],[])

    def test_group_filter_precedes_limit(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'memory.db';make_events(db,100,group='other')
            c=sqlite3.connect(db)
            c.execute("INSERT INTO events VALUES(101,'s','wanted','u','n','qualifying message',0,0)");c.commit();c.close()
            rows,last=learning.read_events(str(db),0,['wanted'],6,300,100)
            self.assertEqual([r['id'] for r in rows],[101]);self.assertEqual(last,101)

    def test_cursor_matches_presented_batch(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'memory.db';make_events(db,100);s=Store(str(Path(td)/'style.db'))
            async def ask(system,user,stage):return '{"keep":[]}'
            r=asyncio.run(learning.process_group(db_path=str(db),group_id='g1',since_id=0,cfg=CFG,ask=ask,store=s))
            self.assertEqual(r['max_id'],40);s.close()

    def test_bom_disable_preserved_and_bad_config_fails_closed(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('style_config',PLUGIN_DIR/'config.py');cfg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cfg)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'config.json';p.write_text('{"enabled":false}',encoding='utf-8-sig')
            self.assertFalse(cfg.load_config(str(p))['enabled'])
            p.write_text('{invalid',encoding='utf-8');self.assertFalse(cfg.load_config(str(p))['enabled'])

    def test_external_lock_skipped_quickly(self):
        import time
        with tempfile.TemporaryDirectory() as td:
            p=str(Path(td)/'style.db');s=Store(p);other=sqlite3.connect(p);other.execute('BEGIN EXCLUSIVE')
            started=time.monotonic()
            try:
                with self.assertRaises(sqlite3.OperationalError):s.approved('style_rules','g1')
                self.assertLess(time.monotonic()-started,.2)
            finally:other.rollback();other.close();s.close()


if __name__ == "__main__":
    unittest.main()
