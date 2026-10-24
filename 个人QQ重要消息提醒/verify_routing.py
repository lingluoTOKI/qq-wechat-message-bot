import asyncio,tempfile,json
from pathlib import Path
from aiohttp import web
from verify_workflows import fixture

async def run():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);r=fixture(root);profile=r.cfg['profile'];uploads=[]
        r.workbench_action(dict(action='notification_targets',file_group='123456789',recipient='987654321'))
        assert r.cfg['target_user']=='987654321' and r.cfg['target_group']=='123456789'
        assert r.cfg['profile']==profile,'routing must not change model cache identity'
        for bad in ('','abc','001234','1234'):
            try:r.workbench_action(dict(action='notification_targets',file_group=bad,recipient='987654321'))
            except web.HTTPBadRequest:pass
            else:raise AssertionError('invalid target accepted')
        assert r.validate_preferences(dict(r.preferences,file_group='111111'))['file_group']=='123456789'
        r.remember_file(dict(platform='qq',source='123',label='test',kind='group'),'m',dict(name='test.pdf',file_id='f',size=3,available=True))
        key=r.full_review_data()['files'][0]['id']
        async def rpc(action,params):
            if action=='get_group_file_url':return {'url':'https://files.qq.com/test'}
            assert action=='upload_group_file';uploads.append(params);return {'file_id':'receipt'}
        r.rpc=rpc
        try:r.full_review_action(dict(action='transfer_files',files=[key],expected_group='917905372'))
        except web.HTTPConflict:pass
        else:raise AssertionError('stale target accepted')
        r.full_review_action(dict(action='transfer_files',files=[key],expected_group='123456789'))
        r.workbench_action(dict(action='notification_targets',file_group='222222222',recipient='987654321'))
        await r.file_task
        assert uploads[0]['group_id']==123456789,'submitted batch target must stay fixed'
        r.db.close();r=fixture(root)
        assert r.cfg['target_user']=='987654321' and r.cfg['target_group']=='222222222'
        assert r.full_review_data()['target_group']==222222222
        assert 'file_group' not in r.cfg['profile']
        r.db.close()
    print('PASS: validated targets, encrypted persistence, live routing, stable upload destination, stale target rejected, preferences preserved')

asyncio.run(run())
