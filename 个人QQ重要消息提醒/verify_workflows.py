"""Offline workflow checks: isolated encrypted databases, no API or QQ sends."""
import asyncio
import copy
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

from relay import Relay, TZ
from vault import seal, unseal


def fixture(root):
    cfg={'budget_mode':'gift_tokens','gift_token_limit':15000000,'gift_baseline_used':0,'target_user':'474496195','target_group':'917905372','qq_account':'483257228','provider':'bigmodel','model':'glm-5.3-flash','api_base':'https://example.invalid','profile':'test identity','max_output_tokens':4096,'input_cny_per_million':.8,'output_cny_per_million':2.8,'wechat_enabled':True,'digest_hour':0}
    (root/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
    (root/'secrets.dpapi').write_text(seal(json.dumps({'callback_token':'test-only'})),encoding='utf-8')
    r=Relay(root);r.account_verified=True
    return r


def topic(text,reference=False):
    return {'summary':text,'deadline':'未明确','action':'' if reference else 'submit','relevant':True,'urgent':not reference,'important':False,'useful':not reference,'reference':reference,'source_time':time.time()}


def result(group,topics):
    return {'id':group['id'],'topics':topics,'reason':'test','summary':'test','deadline':'','action':'','relevant':bool(topics),**{flag:any(t.get(flag) for t in topics) for flag in ('urgent','important','useful','reference')}}


async def run():
    with tempfile.TemporaryDirectory() as name:
        r=fixture(Path(name));db=r.db;today=datetime.now(TZ).strftime('%Y-%m-%d')
        # Seven independent items: five sent, two still pending, all seven visible.
        for i in range(7):
            value={'platform':'qq','source':str(i),'summary':'notice '+str(i),'deadline':'未明确','action':'submit'}
            db.execute('INSERT INTO digest VALUES(?,?,?,?)',(str(i),today,seal(json.dumps(value)),'pending'))
        db.commit();r.queue_daily_digest()
        assert db.execute("SELECT count(*) FROM digest WHERE status='pending'").fetchone()[0]==2
        payload=json.loads(unseal(db.execute('SELECT payload FROM delivery').fetchone()[0]))
        assert payload['params']['user_id']==474496195 and '2' in payload['params']['message'][0]['data']['text']
        # A QQ message received at capacity survives in the archive and is replayed once.
        db.execute('DELETE FROM events')
        db.executemany("INSERT INTO events(id,payload,at,status) VALUES(?,?,?,'pending')",[(str(i),'unused',time.time()) for i in range(500)]);db.commit()
        r.ingest('qq','group','111','222','class','明天提交材料','overflow-test')
        assert db.execute('SELECT count(*) FROM qq_archive').fetchone()[0]==1
        db.execute("DELETE FROM events WHERE id='0'");db.commit();r.refill_qq()
        assert db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]==500
        r.refill_qq();assert db.execute('SELECT count(*) FROM events').fetchone()[0]==500
        # One conversation can produce independent notifications, all private.
        db.execute('DELETE FROM events');db.execute('DELETE FROM delivery');db.commit()
        r.ingest('qq','group','333','222','class','明天报名和提交材料','two-topics')
        r.ingest('qq','group','444','222','other','ordinary discussion','ordinary')
        async def fake_request(session,groups,history=False):
            return {'items':[result(g,[topic('registration'),topic('materials')]) for g in groups]}
        r._analyze_request=fake_request
        await r.batch(None,urgent_only=True)
        assert db.execute('SELECT count(*) FROM matters').fetchone()[0]==2
        for blob, in db.execute('SELECT payload FROM delivery'):
            value=json.loads(unseal(blob));assert value['action']=='send_private_msg' and value['params']['user_id']==474496195
        assert db.execute('SELECT count(*) FROM delivery').fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]==1
        # Same messages and canonical timestamps reuse extraction across both uses.
        db.execute('DELETE FROM analysis_cache');db.commit();calls=[]
        async def counting(session,groups,history=False):
            calls.append(copy.deepcopy(groups));return {'items':[result(g,[topic('fact',True)]) for g in groups]}
        r._analyze_request=counting
        g={'id':'a','platform':'qq','kind':'group','source':'888','label':'class','messages':[{'text':str(i),'sender':'x','time':1700000000+i} for i in range(5)]}
        await r.analyze(None,[g],True)
        live=copy.deepcopy(g);live['id']='different';live['messages'][0]['time']=datetime.fromtimestamp(1700000000,TZ).isoformat()
        assert (await r.analyze(None,[live],False))['items'][0]['id']=='different' and len(calls)==1
        new=copy.deepcopy(g);new['messages'].append({'text':'new','sender':'x','time':1700000006})
        await r.analyze(None,[new],True);assert len(calls[-1][0]['messages'])==3
        # Midnight does not expire factual history; live alert eligibility refreshes.
        row=db.execute('SELECT k,payload FROM analysis_cache WHERE k=?',(r.cache_identity(new,True),)).fetchone();value=json.loads(unseal(row[1]));value['day']='2000-01-01'
        db.execute('UPDATE analysis_cache SET payload=? WHERE k=?',(seal(json.dumps(value)),row[0]));db.commit()
        before=len(calls);await r.analyze(None,[new],True);assert len(calls)==before
        await r.analyze(None,[new],False);assert len(calls)==before+1
        # A sliding window reuses retained evidence and drops out-of-window topics.
        db.execute('DELETE FROM analysis_cache');db.commit()
        async def rolling(session,groups,history=False):
            calls.append(copy.deepcopy(groups));topics=[]
            for stamp in (1700000000,1700000003):
                t=topic(str(stamp),True);t.update(source_time=stamp,time_approximate=False);topics.append(t)
            return {'items':[result(groups[0],topics)]}
        r._analyze_request=rolling
        await r.analyze(None,[g],True)
        shifted=copy.deepcopy(g);shifted['messages']=shifted['messages'][2:]+[{'text':'next','sender':'x','time':1700000005}]
        await r.analyze(None,[shifted],True)
        assert len(calls[-1][0]['previous_analysis']['topics'])==1
        assert calls[-1][0]['previous_analysis']['topics'][0]['source_time']==1700000003
        # Source and completion controls persist without touching messages at QQ.
        key=db.execute('SELECT id FROM matters LIMIT 1').fetchone()[0];db.execute('UPDATE matters SET done=1 WHERE id=?',(key,));db.commit()
        assert any(v['id']==key and v['done'] for v in r.operations_data()['matters'])
        # Explicit recovery endpoints reset failed analysis, never silently replay uncertain sends.
        class Request:
            headers={'Authorization':'Bearer test-only'}
            method='POST'
            def __init__(self,value):self.value=value
            async def json(self):return self.value
        db.execute("INSERT INTO events(id,payload,at,status,retries) VALUES(?,?,?,'analysis_failed',3)",('failure',seal(json.dumps({'platform':'qq','label':'test'})),time.time()));db.commit()
        await r.operations(Request({'action':'retry_analysis','id':'failure'}))
        assert db.execute("SELECT status,retries FROM events WHERE id='failure'").fetchone()==('pending',0)
        db.execute("INSERT INTO delivery VALUES(?,?,?,?)",('uncertain','encrypted','uncertain',time.time()));db.commit()
        await r.operations(Request({'action':'dismiss_delivery','id':'uncertain'}))
        assert db.execute("SELECT status FROM delivery WHERE id='uncertain'").fetchone()[0]=='confirmed'
        db.execute("INSERT INTO token_ledger VALUES('old',500,'reserved',?)",(time.time()-600,))
        db.execute("INSERT INTO token_ledger VALUES('active',20,'reserved',?)",(time.time(),));db.commit()
        await r.calibrate(Request({'remaining':14999900}))
        assert db.execute("SELECT tokens,status FROM token_ledger WHERE id='old'").fetchone()==(0,'reconciled')
        assert db.execute("SELECT tokens,status FROM token_ledger WHERE id='active'").fetchone()==(20,'reserved')
        assert db.execute('SELECT sum(tokens) FROM token_ledger').fetchone()[0]==100
        assert db.execute('SELECT count(*) FROM usage_reconciliation').fetchone()[0]==1
        db.close()
    print('PASS: daily overflow, archived QQ replay, independent topics, private-only routing, cross-use cache, incremental context, midnight validity, persisted completion')


if __name__=='__main__':asyncio.run(run())
