import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins'))
from dafeiyu_runtime.balance import balance_text, validate


class BalanceTests(unittest.TestCase):
    def test_cache_failure_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'config.json';db=Path(folder)/'balance.db'
            config.write_text(json.dumps({'provider_sources':[{'api_base':'https://api.deepseek.com/v1','key':['secret']}]}))
            payload={'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'12.34','granted_balance':'2','topped_up_balance':'10.34'}]}
            calls=[]
            def fetch(key):calls.append(key);return payload
            def fail(key):raise RuntimeError('secret should not appear')
            self.assertIn('12.34',balance_text(config,db,fetch,now=1000))
            self.assertIn('12.34',balance_text(config,db,fail,now=1010))
            self.assertIn('旧快照',balance_text(config,db,fail,now=1070))
            self.assertNotIn('旧快照',balance_text(config,db,fetch,now=1080))
            self.assertEqual(len(calls),2)
            self.assertNotIn(b'secret',db.read_bytes())

    def test_invalid_numbers(self):
        with self.assertRaises(ValueError):
            validate({'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'NaN','granted_balance':'0','topped_up_balance':'0'}]})

    def test_failure_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'c.json';config.write_text(json.dumps({'provider_sources':[{'api_base':'https://api.deepseek.com','key':'secret'}]}))
            def fail(key):raise RuntimeError('secret')
            text=balance_text(config,Path(folder)/'b.db',fail,now=1000)
            self.assertIn('金额未知',text);self.assertNotIn('secret',text)

    def test_proxy_never_receives_official_key(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'c.json';config.write_text(json.dumps({'provider_sources':[{'api_base':'https://example.com','key':'secret'}]}))
            def fetch(key):self.fail('must not fetch')
            self.assertIn('未找到唯一',balance_text(config,Path(folder)/'b.db',fetch))


if __name__=='__main__':unittest.main()
