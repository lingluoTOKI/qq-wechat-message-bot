"""Isolated tests: no real platform messages, models or files."""
import asyncio,json,tempfile,time,copy
from pathlib import Path
from unittest.mock import patch
from verify_workflows import fixture,topic,result
from vault import unseal
from aiohttp import web
from reliability import DeliveryRejected

async def run():
 with tempfile.TemporaryDirectory() as temp:
    r=fixture(Path(temp));now=time.time();calls=[];models=[]
    async def noop(*args,**kwargs):pass
    r.deliver=noop
    async def rpc(action,p):
        calls.append((action,p))
        if action=='get_group_list':return [{'group_id':111,'group_name':'class'},{'group_id':222,'group_name':'exam'}]
        if action=='get_friend_list':return [{'user_id':333,'nickname':'friend'}]
        if action.endswith('msg_history'):
            return {'messages':[] if p['message_id'] else [dict(message_id=10,time=now-10,user_id=1,message=[{'type':'text','data':{'text':'notice'}},{'type':'file','data':{'name':'notice.pdf','file_id':'file1','size':4}}]),dict(message_id=9,time=now-90000,user_id=1,message=[{'type':'file','data':{'name':'old.pdf','file_id':'old','size':4}}])]}
        if action=='upload_group_file':return {'file_id':'receipt'}
        if action=='get_group_file_url':return {'url':'https://files.qq.com/test'}
        raise AssertionError(action)
    r.rpc=rpc
    async def model(session,gs,history=False):
        models.append(copy.deepcopy(gs));return {'items':[result(g,[topic('result',True)]) for g in gs]}
    r._analyze_request=model
    r.remember_group('qq','111','class');r.db.execute("UPDATE watched_groups SET focused=1 WHERE id='qq:111'");r.db.commit()
    scope={'platform':'qq','kind':'group','selection':'focused','start':now-3600,'end':now,'mode':'files'}
    with patch('full_review.wechat_page',return_value=[]):
        r.plus_action({'action':'scan_preview','scope':scope});await r.preview_task
        assert r.scan_preview['selected_count']==1 and not models and not any(a.endswith('msg_history') for a,p in calls)
        r.full_review_action({'action':'full_start','scope':scope});await r.full_task
    assert not models and r.full_job['state']=='done'
    assert r.full_job['total_sources']==1 and r.full_review_data()['file_count']==1
    assert r.full_review_data()['files'][0]['name']=='notice.pdf'
    assert r.full_review_data(filters={'q':'old'})['file_count']==0
    # Source focus changes only its cache, and exact corrections are reversible.
    a=dict(id='a',platform='qq',kind='group',source='111',label='class',messages=[])
    b=dict(a,id='b',source='222',label='exam')
    keys=(r.cache_identity(a,True),r.cache_identity(b,True))
    r.plus_action({'action':'source_focus','id':'qq:111','focus':'class notices only'})
    assert r.cache_identity(a,True)!=keys[0] and r.cache_identity(b,True)==keys[1]
    item=topic('submit materials');key=r.store_matter(a,item)
    r.plus_action({'action':'matter_feedback','id':key,'choice':'irrelevant'})
    assert r.db.execute('SELECT done FROM matters WHERE id=?',(key,)).fetchone()[0]==1
    assert not r.apply_topic_feedback(a,dict(item))['relevant']
    assert r.apply_topic_feedback(b,dict(item))['relevant']
    r.plus_action({'action':'matter_feedback','id':key,'choice':'undo'})
    assert not r.db.execute('SELECT done FROM matters WHERE id=?',(key,)).fetchone()[0]
    other=r.store_matter(a,topic('other notice'))
    r.plus_action({'action':'matter_feedback','id':key,'choice':'duplicate','duplicate_of':other})
    assert r.plus_data()['feedback'][key]['duplicate_of']==other
    r.plus_action({'action':'matter_feedback','id':key,'choice':'undo'})
    r.plus_action({'action':'deadline_reminder','id':key,'deadline':now+3*86400,'lead':86400})
    due=r.db.execute('SELECT due FROM reminders WHERE matter=? AND state=?',(key,'scheduled')).fetchone()[0]
    assert abs(due-(now+2*86400))<1
    # Three notification tiers: one immediate, one daily, one retained only.
    r.db.execute('DELETE FROM events');r.db.execute('DELETE FROM delivery');r.db.execute('DELETE FROM digest');r.db.commit()
    r.ingest('qq','group','444','9','tier-test','明天提交材料报名通知','tiers')
    async def tiers(session,gs,history=False):
        urgent=topic('urgent');important=topic('non urgent');important.update(urgent=False,important=True,useful=False)
        reference=topic('reference',True)
        return {'items':[result(g,[urgent,important,reference]) for g in gs]}
    r._analyze_request=tiers
    await r.batch(None)
    assert r.db.execute('SELECT count(*) FROM delivery').fetchone()[0]==1
    assert r.db.execute("SELECT count(*) FROM digest WHERE status='pending'").fetchone()[0]==1
    # Different file names with identical bytes must upload once to a given group.
    base=Path(temp)/'files';base.mkdir();(base/'a.pdf').write_bytes(b'identical');(base/'b.pdf').write_bytes(b'identical')
    g=dict(platform='wechat',kind='group',source='test@chatroom',label='class')
    for name in ('a.pdf','b.pdf'):r.remember_file(g,name,dict(name=name,path=str(base/name),base=str(base),size=9,available=True))
    ids=[v['id'] for v in r.full_review_data()['files'] if v['platform']=='wechat']
    calls.clear();await r.check_files(ids);await r.transfer_files(ids)
    assert len([a for a,p in calls if a=='upload_group_file'])==1
    assert {v['status'] for v in r.full_review_data()['files'] if v['platform']=='wechat'}=={'sent','duplicate'}
    assert len(r.plus_data()['file_deliveries'])==2
    assert all(v['target']=='917905372' for v in r.plus_data()['file_deliveries'])
    # Clear failure is retryable; ambiguous sends stay protected.
    r.remember_file(a,'failed',dict(name='fail.pdf',file_id='fail',available=True))
    failid=next(v['id'] for v in r.full_review_data()['files'] if v['name']=='fail.pdf')
    async def rejected(action,p):
        if action=='upload_group_file':raise DeliveryRejected()
        return await rpc(action,p)
    r.rpc=rejected;await r.transfer_files([failid]);assert next(v['status'] for v in r.full_review_data()['files'] if v['id']==failid)=='failed'
    # Failed sources can retry without rereading successful sources.
    r.rpc=rpc;r.batch=noop
    original=r.read_full_page
    async def failing(group):raise RuntimeError('offline fixture')
    r.read_full_page=failing
    with patch('full_review.wechat_page',return_value=[]):
        r.full_review_action({'action':'full_start','scope':scope});await r.full_task
    assert r.full_job['state']=='partial' and len(r.full_job['failed_sources'])==1
    r.read_full_page=original;r.plus_action({'action':'full_retry'});await r.full_task
    assert r.full_job['state']=='done'
    visited_retry=[]
    async def empty_page(group):visited_retry.append(group['source']);return [],[],0,True
    r.read_full_page=empty_page
    r.full_job.update(state='paused',sources=[a,b],source_index=0,failed_sources=[a])
    r.plus_action({'action':'full_retry'});await r.full_task
    assert visited_retry==['111','222'],'retry must preserve untouched sources'
    r.read_full_page=original
    # Stop request during a model batch completes that request then stops.
    async def stop_model(session,gs,history=False):
        r.plus_action({'action':'full_stop'});return {'items':[result(g,[topic('stop result',True)]) for g in gs]}
    r.analyze=stop_model
    with patch('full_review.wechat_page',return_value=[]):
        r.full_review_action({'action':'full_start','scope':dict(scope,mode='history')});await r.full_task
    assert r.full_job['state']=='cancelled'
    # Skip the selected source, without skipping its successor.
    visited=[]
    async def skipping(group):
        visited.append(group['source'])
        if group['source']=='111':r.plus_action({'action':'full_skip'});return [],['marker'],1,False
        return [],[],0,True
    r.read_full_page=skipping
    with patch('full_review.wechat_page',return_value=[]):
        r.full_review_action({'action':'full_start','scope':dict(scope,selection='all')});await r.full_task
    assert visited==['111','222']
    # Concurrent send workers cannot send the same pending record twice.
    from relay import Relay
    r.deliver=Relay.deliver.__get__(r,Relay);r.db.execute('DELETE FROM delivery');r.db.commit()
    from vault import seal
    r.db.execute('INSERT INTO delivery VALUES(?,?,?,?)',('once',seal(json.dumps({'params':{'message':[{'type':'text','data':{'text':'fixture'}}]}})),'pending',time.time()));r.db.commit()
    sends=[]
    async def sending(action,p):sends.append(p);await asyncio.sleep(.02);return {'message_id':'fixture'}
    r.rpc=sending;await asyncio.gather(r.deliver(),r.deliver())
    assert len(sends)==1 and sends[0]['user_id']==474496195
    r.db.close()
 print('PASS: scoped preview, date bounds, zero-model file scan, isolated preferences/feedback, three tiers, deadline offsets, file dedup/audit, retry and stop')

asyncio.run(run())
