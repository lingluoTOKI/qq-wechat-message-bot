"""Offline checks; fake gateway only, no model or real notifications."""
import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from verify_workflows import fixture,topic
from reliability import DeliveryRejected,deadline_ts
from vault import seal,unseal
from restore_backup import restore

async def run():
    with tempfile.TemporaryDirectory() as directory:
        r=fixture(Path(directory));now=time.time()
        g=dict(platform='qq',source='111',label='test class',messages=[])
        t=topic('提交班级统计表');t.update(deadline='2026-11-01 18:00',source_time=now-10)
        key=r.store_matter(g,t)
        r.reliability_action(dict(action='schedule_reminder',id=key,due=now+3600,hide=True))
        t.update(deadline='2026-11-02 18:00',source_time=now)
        assert r.store_matter(g,t)==key
        assert not r.db.execute("SELECT 1 FROM reminders WHERE state='scheduled'").fetchone()
        v=json.loads(unseal(r.db.execute('SELECT payload FROM matters WHERE id=?',(key,)).fetchone()[0]))
        assert v['revision']==1 and len(v['revisions'])==1
        # A stale history review cannot overwrite a corrected deadline.
        old=dict(t,deadline='2026-11-01 18:00',source_time=now-30)
        assert r.store_matter(g,old)==key
        assert json.loads(unseal(r.db.execute('SELECT payload FROM matters').fetchone()[0]))['deadline']==t['deadline']
        assert r.store_matter(dict(g,source='222'),t)!=key
        assert r.store_matter(g,dict(t,summary='提交报名材料'))!=key
        child=r.store_matter(g,dict(t,summary='补充统计表填写说明'))
        r.reliability_action(dict(action='link_matter',id=key,child=child))
        assert r.db.execute('SELECT parent FROM matter_links WHERE child=?',(child,)).fetchone()[0]==key
        r.reliability_action(dict(action='unlink_matter',id=key,child=child))
        assert not r.db.execute('SELECT 1 FROM matter_links').fetchone()
        r.reliability_action(dict(action='schedule_reminder',id=key,due=now+120))
        r.db.execute("UPDATE reminders SET due=? WHERE state='scheduled'",(now-10,));r.db.commit()
        r.queue_reminders();r.queue_reminders()
        assert r.db.execute('SELECT count(*) FROM delivery').fetchone()[0]==1
        calls=[]
        async def reject(action,params):calls.append(params);raise DeliveryRejected()
        r.rpc=reject
        for _ in range(3):
            r.db.execute('UPDATE delivery_meta SET next_attempt=0');r.db.commit();await r.deliver()
        assert len(calls)==3 and r.db.execute('SELECT status FROM delivery').fetchone()[0]=='failed'
        assert all(c['user_id']==474496195 for c in calls)
        delivery_id=r.db.execute('SELECT id FROM delivery').fetchone()[0]
        r.reliability_action(dict(action='retry_delivery_failed',id=delivery_id))
        async def timeout(*args):raise TimeoutError()
        r.rpc=timeout;await r.deliver();await r.deliver()
        assert r.db.execute('SELECT status FROM delivery').fetchone()[0]=='uncertain'
        r.db.execute("UPDATE delivery SET status='pending'");r.db.commit()
        async def success(*args):return {'message_id':123}
        r.rpc=success;await r.deliver()
        assert r.db.execute('SELECT status,payload FROM delivery').fetchone()==('sent','')
        assert r.reliability_data()['deliveries'][0]['status']=='sent'
        r.reliability_action(dict(action='schedule_reminder',id=key,due=now+3600))
        r.db.execute('UPDATE matters SET at=? WHERE id=?',(now-40*86400,key));r.db.commit()
        r.clean_saved_history();assert r.db.execute('SELECT 1 FROM matters WHERE id=?',(key,)).fetchone()
        r.create_backup(True);files=r.backup_data()['files'];assert len(files)==1
        assert 'valid' in restore(r.root,files[0]['name'])
        r.db.execute("INSERT INTO token_ledger VALUES('post-backup',123,'measured',?)",(now,));r.db.commit()
        assert deadline_ts('2026-11-31 12:00',now) is None
        assert deadline_ts('明天12:00',now,True) is None
        r.db.close()
        from unittest.mock import patch
        with patch('restore_backup.socket.socket') as socket:
            socket.return_value.__enter__.return_value.connect_ex.return_value=1
            assert 'Restored' in restore(r.root,files[0]['name'],True)
        from contextlib import closing
        with closing(sqlite3.connect(r.root/'state.sqlite')) as restored:
            assert restored.execute("SELECT tokens FROM token_ledger WHERE id='post-backup'").fetchone()[0]==123
            assert restored.execute("SELECT count(*) FROM reminders WHERE state='scheduled'").fetchone()[0]==0
    print('PASS: conservative revisions, stale history guard, reminder persistence/dedup, bounded retry, timeout ambiguity, private routing, retention, backup validation')

if __name__=='__main__':asyncio.run(run())
