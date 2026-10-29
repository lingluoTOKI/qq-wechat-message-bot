import logging
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from test_runtime import function,ROOT


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_gets_standard_reply_nonadmin_is_silent(self):
        cb=types.SimpleNamespace(_is_admin=lambda e:e.admin,ctxbudget_text=lambda e:'actual diagnostics')
        fn=function(ROOT/'plugins/astrbot_plugin_dafeiyu_help/main.py','dg_ctxbudget_cmd',{'_cb':cb,'logger':logging.getLogger('test')})
        for admin in [True,False]:
            event=types.SimpleNamespace(admin=admin,stop_event=lambda:None,plain_result=lambda s:s)
            result=[r async for r in fn(object(),event)]
            self.assertEqual(result,['actual diagnostics'] if admin else [])
    async def test_recent_usage_scoped_to_request_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stats.db';c=sqlite3.connect(path)
            c.execute('CREATE TABLE provider_stats(start_time,provider_id,token_input_other,token_input_cached,token_output,umo)')
            c.executemany('INSERT INTO provider_stats VALUES(?,?,?,?,?,?)',[(1,'deepseek/x',10,90,5,'a'),(2,'deepseek/x',999,0,5,'b')]);c.commit();c.close()
            fn=function(ROOT/'plugins/astrbot_plugin_dafeiyu_help/ctxbudget.py','_recent_usage',{'sqlite3':sqlite3,'STATS_DB':str(path),'logger':logging.getLogger('test')})
            self.assertEqual(fn('a')['total'],100);self.assertEqual(fn('missing'),{})
