import tempfile,json
from pathlib import Path
from verify_workflows import fixture,topic
from reference_library import classify,save_metadata,library_data
from aiohttp import web
with tempfile.TemporaryDirectory() as temp:
 r=fixture(Path(temp))
 cases=[('DMA传输方式：每准备好一个字发送一次','考研学习'),('群友转述同济招生名额缩招','报考与院校'),('思政委员评优材料','班级与学工'),('讲义.pdf下载','资料与文件'),('宿舍快递通知','个人与生活'),('群友说楼在震动','待分类')]
 for text,c in cases:assert classify({'summary':text})['category']==c
 assert classify({'summary':'官方招生通知'})['trust']=='来源不明'
 assert classify({'summary':'群友转述招生名额'})['uncertain']
 g=dict(id='g',platform='qq',kind='group',source='111',label='group',messages=[])
 key=r.store_matter(g,topic('DMA学习经验',True))
 before=r.db.execute('SELECT done,starred,payload FROM matters WHERE id=?',(key,)).fetchone()
 save_metadata(r.db,dict(id=key,category='考研学习',tags=['408','自定义']))
 assert library_data(r.db)[key]['manual']
 assert r.db.execute('SELECT done,starred,payload FROM matters WHERE id=?',(key,)).fetchone()==before
 assert library_data(r.db)[key]['tags']==['408','自定义']
 try:save_metadata(r.db,dict(id=key,category='不存在',tags=[]));raise AssertionError()
 except web.HTTPBadRequest:pass
 save_metadata(r.db,dict(id=key,reset=True));assert not library_data(r.db)[key]['manual']
 r.db.close()
print('reference library isolated checks passed; no models or messages')

