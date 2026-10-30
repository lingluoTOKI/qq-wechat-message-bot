"""Apply user-reviewed interest retention and the confirmed Yaobai bot identity."""
import hashlib,importlib.util,json,tarfile,time
from pathlib import Path
import paramiko

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
BASE='/opt/bot-stack/astrbot/data/'
FILES={'plugins/dafeiyu_runtime/profile_quality.py':'pylibs/dafeiyu_runtime/profile_quality.py',
       **{'plugins/astrbot_plugin_dafeiyu_daily_memory/'+name:'plugins/astrbot_plugin_dafeiyu_daily_memory/'+name
          for name in ['main.py','store.py','extract.py','recall.py','birthday_watch.py','metadata.yaml']}}
REVISIONS={
 29:('喜好','曾向小洛询问《英雄联盟》的英雄连招'),
 34:('喜好','曾找小洛讨论游戏王和LOL相关玩法'),
 36:('喜好','曾向小洛询问塞尔达系列的游戏原声音乐'),
 37:('喜好','曾向小洛询问电子音乐并请求推荐'),
 38:('喜好','曾向小洛询问《EVA》的评价'),
 39:('喜好','曾和小洛聊电影，询问小洛喜欢的电影'),
 40:('关系','与小洛聊天时，会沿着回答继续追问相关内容'),
 54:('喜好','曾讨论通过插件扩展自己的AI助手功能，并畅想安装很多插件'),
 67:('喜好','曾向小洛求推荐游戏、动漫和歌曲'),
 70:('喜好','与「爱吃饭的大肥鱼」互动时，希望回答认真且符合提出的篇幅要求'),
 75:('喜好','曾请小洛用语音说话，并让它说“今天也要加油哦”'),
 77:('喜好','曾向小洛询问是否会玩《明日方舟》'),
 81:('关系','曾与小洛开讨要和投喂token的玩笑')}
SUMMARIES={
 'AoAo':'喜欢音乐动漫，会向小洛询问推荐并继续追问',
 '后排靠窗 王の故乡':'MC玩家，会找群友联机，也会和小洛开亲昵的玩笑',
 'CanTabi1e':'玩明日方舟、游戏王和LOL，会找小洛聊游戏玩法，常担任兜底位',
 'Ryan.':'正在自制AI助手，也玩MC，并讨论过用插件扩展项目功能',
 '耀白':'自行部署「爱吃饭的大肥鱼」，会讨论其功能并与它互动'}


def main():
    baseline=json.loads((ROOT/'review/final-source-baseline.json').read_text(encoding='utf-8'))
    spec=importlib.util.spec_from_file_location('registry',ROOT/'scripts/tests/verify_acceptance_deployment.py')
    registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)
    for p in registry.MANIFESTS:
        for row in json.loads(p.read_text(encoding='utf-8'))['files']:baseline[row['target']]=row['after']
    archive=ROOT/'review/interest-review-tests.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for src in FILES:
            if src.endswith('.py'):compile((ROOT/src).read_bytes(),src,'exec')
            t.add(ROOT/src,arcname=src)
        t.add(ROOT/'tests/plugin_optimization/test_profile_quality.py',arcname='tests/plugin_optimization/test_profile_quality.py')
    c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('101.132.104.41',username='root',key_filename=str(ROOT/'.robot-nopass.pem'),timeout=15)
    def run(code):
        i,o,e=c.exec_command('docker exec -i astrbot python -',timeout=120)
        i.write(code);i.channel.shutdown_write();out=o.read().decode();err=e.read().decode()
        if o.channel.recv_exit_status():raise RuntimeError(out+err)
        print(out,flush=True)
    try:
        with c.open_sftp() as s:s.put(str(archive),BASE+'temp/interest-review-tests.tar.gz')
        run('''import pathlib,subprocess,tarfile,sys,importlib
stage=pathlib.Path('/tmp/profile-quality-acceptance')
with tarfile.open('/AstrBot/data/temp/interest-review-tests.tar.gz') as t:t.extractall(stage,filter='data')
r=subprocess.run(['python','-m','unittest','discover','-s','tests/plugin_optimization','-v'],cwd=stage,capture_output=True,text=True)
pathlib.Path('/AstrBot/data/temp/interest-review-tests.log').write_text(r.stdout+r.stderr)
print((r.stdout+r.stderr)[-450:]);assert r.returncode==0
sys.path.insert(0,str(stage/'plugins'));importlib.import_module('astrbot_plugin_dafeiyu_daily_memory.main')
print('Actual framework import passed')
''')
        backup='/opt/bot-stack/backups/interest-review-'+time.strftime('%Y%m%d-%H%M%S')
        m={'backup':backup,'files':[]}
        with c.open_sftp() as s:
            s.mkdir(backup,mode=0o700)
            for idx,(src,dest) in enumerate(FILES.items()):
                target=BASE+dest
                with s.open(target,'rb') as f:before=f.read()
                assert hashlib.sha256(before).hexdigest()==baseline[target],target
                row={'target':target,'before':baseline[target],'after':hashlib.sha256((ROOT/src).read_bytes()).hexdigest(),'backup':backup+f'/{idx}.before'}
                with s.open(row['backup'],'wb') as f:f.write(before)
                m['files'].append(row)
        run('''import sqlite3,os
src=sqlite3.connect('file:/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db?mode=ro',uri=True)
dst=sqlite3.connect('/AstrBot/data/temp/interest-review-before.db');src.backup(dst);dst.close();src.close()
os.chmod('/AstrBot/data/temp/interest-review-before.db',0o600)
print('Consistent database backup created')
''')
        with c.open_sftp() as s:
            s.rename(BASE+'temp/interest-review-before.db',backup+'/memory-before.db')
            for src,dest in FILES.items():
                target=BASE+dest;s.put(str(ROOT/src),target+'.interest-new');s.posix_rename(target+'.interest-new',target)
            for row in m['files']:
                with s.open(row['target'],'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==row['after']
        (ROOT/'review/interest-review-deployment.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
        code='''import sys,json,time,pathlib
sys.path.insert(0,'/AstrBot/data/plugins');sys.path.insert(0,'/AstrBot/data/pylibs')
from astrbot_plugin_dafeiyu_daily_memory.store import Store
from dafeiyu_runtime.profile_quality import reason
store=Store('/AstrBot/data/plugin_data/dafeiyu_daily_memory/memory.db')
assert not store.get_meta('interest_review_20261029'),'Review already applied'
revisions=json.loads(REVISIONS);summaries=json.loads(SUMMARIES)
people={}
for name in summaries:
 rows=store.db.execute('SELECT * FROM people WHERE name=?',(name,)).fetchall();assert len(rows)==1,name
 people[name]=rows[0]
for fid,(category,text) in revisions.items():
 row=store.db.execute('SELECT * FROM facts WHERE id=?',(int(fid),)).fetchone()
 assert row is not None and row['superseded_by'] is None,fid
 assert not reason(text,category=category),(fid,text)
for name,text in summaries.items():assert not reason(text,summary=True),(name,text)
report={'restored_versions':[],'summary_revisions':[],'raw_deleted':0,'model_calls':0}
before=store.db.execute('SELECT count(*) FROM events').fetchone()[0]
with store.db:
 for fid,(category,text) in revisions.items():
  old=store.db.execute('SELECT * FROM facts WHERE id=?',(int(fid),)).fetchone()
  target='爱吃饭的大肥鱼' if int(fid)==70 else ('用户自己的AI助手' if int(fid)==54 else '小洛')
  ref=json.dumps({'basis':'user_audit_feedback','reviewed_from_fact':int(fid),'signal':'interest_or_interaction',
      'bot_name':target,'attribution':'review_of_legacy_record_not_literal_message_evidence'},ensure_ascii=False)
  new=store.add_fact(old['sender_id'],text,category,old['confidence'],source='user_review',source_ref=ref,supersedes=[int(fid)])
  store.db.execute("UPDATE fact_quality SET status='revised',reason=?,checked_at=? WHERE fact_id=?",('用户审核：已保留为校正版本',time.time(),int(fid)))
  report['restored_versions'].append({'old_id':int(fid),'new_id':new,'text':text,'bot_name':target})
 for name,text in summaries.items():
  p=people[name]
  if text!=p['summary']:
   cur=store.db.execute('INSERT INTO profile_revisions(sender_id,old_summary,new_summary,reason,created_at) VALUES(?,?,?,?,?)',
      (p['sender_id'],p['summary'],text,'用户审核：保留兴趣互动并区分机器人实例',time.time()))
   store.db.execute('UPDATE people SET summary=?,summary_at=?,updated_at=? WHERE sender_id=?',(text,time.time(),time.time(),p['sender_id']))
   report['summary_revisions'].append({'name':name,'revision_id':cur.lastrowid,'old':p['summary'],'new':text})
 context={'bot_name':'爱吃饭的大肥鱼','relationship':'耀白自行部署','source':'user_confirmed','confirmed_at':time.time()}
 key='person_bot_context:'+str(people['耀白']['sender_id'])
 report['old_context']=store.get_meta(key);report['context_key']=key;report['context']=context
 store.db.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(key,json.dumps(context,ensure_ascii=False)))
 store.db.execute('INSERT INTO meta(k,v) VALUES(?,?)',('interest_review_20261029',str(time.time())))
assert store.db.execute('SELECT count(*) FROM events').fetchone()[0]>=before
report['still_quarantined']=store.db.execute("SELECT count(*) FROM fact_quality q JOIN facts f ON f.id=q.fact_id WHERE q.status='quarantined' AND f.superseded_by IS NULL").fetchone()[0]
store.close();pathlib.Path('/AstrBot/data/temp/interest-review-applied.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('Restored corrected records:',len(report['restored_versions']),'remaining technical quarantines:',report['still_quarantined'])
'''.replace('REVISIONS',repr(json.dumps(REVISIONS,ensure_ascii=False))).replace('SUMMARIES',repr(json.dumps(SUMMARIES,ensure_ascii=False)))
        run(code)
        run('''import json,time,urllib.request,jwt
d=json.load(open('/AstrBot/data/cmd_config.json',encoding='utf-8-sig'))['dashboard']
token=jwt.encode({'username':d['username'],'exp':int(time.time())+180},d['jwt_secret'],algorithm='HS256')
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/reload',data=json.dumps({'name':'astrbot_plugin_dafeiyu_daily_memory'}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
assert json.load(urllib.request.urlopen(r,timeout=90)).get('status')=='ok'
r=urllib.request.Request('http://127.0.0.1:6185/api/plugin/detail?name=astrbot_plugin_dafeiyu_daily_memory',headers={'Authorization':'Bearer '+token})
x=json.load(urllib.request.urlopen(r,timeout=30))['data'];assert x['activated'] and x['version']=='v0.4.1'
print('Memory plugin v0.4.1 active')
''')
        with c.open_sftp() as s:
            for name in ['applied.json','tests.log']:s.get(BASE+'temp/interest-review-'+name,str(ROOT/'review'/('interest-review-'+name)))
        print('Verified source files:',len(m['files']),'Backup:',backup)
    finally:c.close()


if __name__=='__main__':main()
