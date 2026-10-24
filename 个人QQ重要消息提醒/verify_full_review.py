"""Offline history/file workflow checks; never accesses real chats or sends files."""
import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from verify_workflows import fixture,topic,result
from full_review import wechat_file
from vault import unseal

async def run():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);r=fixture(root);calls=[];uploads=[]
        async def noop(*args,**kwargs):pass
        r.batch=noop;r.deliver=noop
        async def analyze(session,groups,history=False):
            calls.extend(m['text'] for g in groups for m in g['messages'])
            return {'items':[result(g,[topic('test notice',True)]) for g in groups]}
        r.analyze=analyze
        def row(n):return dict(message_id=n,time=1700000000+n,user_id=9,message=[{'type':'text','data':{'text':str(n)}}])
        async def rpc(action,params):
            if action=='get_group_list':return [{'group_id':123,'group_name':'fixture'}]
            if action=='get_friend_list':return []
            if action=='get_group_msg_history':
                anchor=params['message_id']
                return {'messages':{0:[row(3),row(2)],2:[row(2),row(1)],1:[row(1)]}[anchor]}
            if action=='get_group_file_url':return {'url':'https://files.qq.com/mock'}
            if action=='upload_group_file':uploads.append(params);return {'file_id':'receipt-fixture'}
            raise AssertionError(action)
        r.rpc=rpc
        with patch('full_review.wechat_page',return_value=[]):
            r.full_review_action({'action':'full_start','scope':{'selection':'all'}});await r.full_task
        assert r.full_job['state']=='done',r.full_job
        assert sorted(calls)==['1','2','3'],calls
        assert r.db.execute('SELECT count(*) FROM full_review_seen').fetchone()[0]==3
        assert r.db.execute('SELECT count(*) FROM matters').fetchone()[0]>0
        with patch('full_review.wechat_page',return_value=[]):
            r.full_review_action({'action':'full_start','scope':{'selection':'all'}});await r.full_task
        assert len(calls)==3,'repeat review must not reanalyze'
        with patch('full_review.wechat_page',return_value=[]):
            r.full_review_action({'action':'full_start','scope':{'selection':'all'}});r.full_review_action({'action':'full_pause'});await r.full_task
            assert r.full_job['state']=='paused'
            r.full_review_action({'action':'full_resume'});await r.full_task
        assert r.full_job['state']=='done'
        group=dict(platform='qq',source='123',label='fixture',kind='group')
        r.remember_file(group,'7',dict(name='notice.pdf',file_id='f1',size=3,available=True))
        key=r.full_review_data()['files'][0]['id']
        r.full_review_action({'action':'transfer_files','files':[key],'expected_group':'917905372','target_group':1});await r.file_task
        assert len(uploads)==1 and uploads[0]['group_id']==917905372
        assert r.full_review_data()['files'][0]['status']=='sent'
        await r.transfer_files([key]);assert len(uploads)==1
        r.remember_file(group,'8',dict(name='other.pdf',file_id='f2',size=3,available=True))
        key=r.full_review_data()['files'][0]['id']
        async def timeout_rpc(action,params):
            if action=='upload_group_file':raise asyncio.TimeoutError()
            return await rpc(action,params)
        r.rpc=timeout_rpc;await r.transfer_files([key])
        assert r.full_review_data()['files'][0]['status']=='uncertain'
        await r.transfer_files([key]);assert len(uploads)==1
        assert 'file_id' not in r.full_review_data()['files'][0]
        stamp=time.time();month=datetime.fromtimestamp(stamp,ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
        folder=root/'files'/month;folder.mkdir(parents=True)
        (folder/'notice[1].pdf').write_bytes(b'abc')
        wx=dict(type='文件/链接/卡片',create_time=stamp,content='<msg><appmsg><type>6</type><title>notice[1].pdf</title><appattach><totallen>3</totallen></appattach></appmsg></msg>')
        assert wechat_file(wx,folder.parent)['available']
        wx['content']=wx['content'].replace('notice[1].pdf','../secret.pdf')
        assert wechat_file(wx,folder.parent) is None
        r.full_job.update(state='running');r.save_full_job();r.db.close()
        again=fixture(root);assert again.full_job['state']=='paused';again.db.close()
    print('PASS: pagination, dedup, pause/resume, persistent checkpoint, fixed target, receipts, uncertain upload, exact file match')

asyncio.run(run())
