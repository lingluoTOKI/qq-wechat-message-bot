"""Isolated migration/settings/scope tests. No live chats, models or sends."""
import asyncio
import json
import tempfile
import time
from pathlib import Path
from aiohttp import web
from verify_workflows import fixture,topic,result
from vault import unseal

async def run():
    with tempfile.TemporaryDirectory() as name:
        root=Path(name);r=fixture(root)
        r.remember_group('qq','111','A',time.time())
        r.remember_group('qq','222','B',time.time())
        r.workbench_action({'action':'group_settings','id':'qq:111','level':'重点关注','delivery':'仅每日汇总'})
        r.remember_group('qq','111','renamed',time.time())
        assert r.group_policy({'platform':'qq','kind':'group','source':'111'})==('重点关注','仅每日汇总')
        g={'id':'a','platform':'qq','kind':'group','source':'111','label':'A','messages':[]}
        key=r.store_matter(g,topic('safe item'))
        r.db.execute('UPDATE matters SET done=1,at=? WHERE id=?',(time.time()-40*86400,key));r.db.commit()
        r.workbench_action({'action':'star','id':key,'enabled':True})
        r.workbench_action({'action':'snooze','id':key,'enabled':True})
        r.store_matter(g,topic('safe item'))
        assert r.db.execute('SELECT done,starred,snoozed FROM matters WHERE id=?',(key,)).fetchone()==(1,1,1)
        r.clean_saved_history();assert r.db.execute('SELECT 1 FROM matters WHERE id=?',(key,)).fetchone()
        p=r.default_preferences();p.update(name='test identity',digest='08:15',timing='全部仅每日汇总',target='test university')
        r.workbench_action({'action':'preferences','id':'settings','preferences':p})
        assert r.cfg['digest_hour']==8 and r.cfg['digest_minute']==15 and not r.allow_immediate(g)
        assert 'test university' not in r.db.execute('SELECT payload FROM workbench_settings').fetchone()[0]
        bad=dict(p,recipient='not-a-user')
        try:r.validate_preferences(bad);raise AssertionError('invalid recipient accepted')
        except web.HTTPBadRequest:pass
        await r.lock.acquire()
        try:
            try:r.workbench_action({'action':'preferences','preferences':p});raise AssertionError('changed active profile')
            except web.HTTPConflict:pass
        finally:r.lock.release()
        # Explicit group scope must not read other known groups, even without archives.
        called=[]
        async def rpc(action,params):called.append(params);return {'messages':[]}
        r.rpc=rpc;r.history_result={'warnings':[]}
        collected=await r.collect_qq_history(1,['qq:222'])
        assert [v['source'] for v in collected]==['222'] and [v['group_id'] for v in called]==[222]
        # All-digest preference prevents immediate notification even for urgent topics.
        r.ingest('qq','group','111','666','A','今天提交材料','test-digest')
        async def analyze(session,groups,history=False):return {'items':[result(v,[topic('urgent but daily')]) for v in groups]}
        r.analyze=analyze
        await r.batch(None)
        assert r.db.execute('SELECT count(*) FROM delivery').fetchone()[0]==0
        assert r.db.execute('SELECT count(*) FROM digest').fetchone()[0]==1
        r.workbench_action({'action':'group_settings','id':'qq:222','level':'暂停分析','delivery':'重要事项即时'})
        r.ingest('qq','group','222','666','B','今天报名截止','test-paused')
        await r.batch(None)
        assert r.db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]==0
        assert r.db.execute("SELECT count(*) FROM events WHERE status='paused'").fetchone()[0]==1
        r.db.close()
        from relay import Relay
        loaded=Relay(root)
        assert loaded.cfg['profile']['target']=='test university'
        assert loaded.db.execute('SELECT done,starred,snoozed FROM matters WHERE id=?',(key,)).fetchone()==(1,1,1)
        assert loaded.operations_data()['preferences']['digest']=='08:15'
        loaded.db.close()
    print('PASS: additive persistence, encrypted preferences, validation, profile lock, selected group scope, pause, daily-only routing')

if __name__=='__main__':asyncio.run(run())
