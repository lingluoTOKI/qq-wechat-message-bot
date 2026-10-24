"""Small encrypted matter store and explicit recovery controls; no model calls."""
import hashlib
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web
from vault import seal, unseal

TZ = ZoneInfo('Asia/Shanghai')


from workbench_store import WorkbenchStore
from reliability import ReliabilityStore

class OpsStore(ReliabilityStore, WorkbenchStore):
    def remember_group(self, platform, conversation, label, seen=None):
        key=platform+':'+str(conversation)
        with self.db:
            self.db.execute("INSERT INTO watched_groups(id,payload,focused) VALUES(?,?,0) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(key,seal(json.dumps({'platform':platform,'conversation':str(conversation),'label':label},ensure_ascii=False))))
            if seen is not None:self.db.execute('UPDATE watched_groups SET last_seen=max(last_seen,?) WHERE id=?',(seen,key))

    def is_focused(self, value):
        if value.get('kind')!='group':return False
        row=self.db.execute('SELECT focused FROM watched_groups WHERE id=?',(value['platform']+':'+str(value['conversation']),)).fetchone()
        return bool(row and row[0])

    def init_ops(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS watched_groups(id TEXT PRIMARY KEY,payload TEXT,focused INTEGER DEFAULT 0)')

        self.db.execute("CREATE TABLE IF NOT EXISTS matters(id TEXT PRIMARY KEY,payload TEXT,at REAL,done INTEGER DEFAULT 0,kind TEXT DEFAULT 'reference',urgent INTEGER DEFAULT 0)")
        columns={row[1] for row in self.db.execute('PRAGMA table_info(matters)')}
        if 'kind' not in columns:
            self.db.execute("ALTER TABLE matters ADD COLUMN kind TEXT DEFAULT 'reference'")
            self.db.execute('ALTER TABLE matters ADD COLUMN urgent INTEGER DEFAULT 0')
            for key,blob in self.db.execute('SELECT id,payload FROM matters').fetchall():
                value=json.loads(unseal(blob));self.db.execute('UPDATE matters SET kind=?,urgent=? WHERE id=?',(value.get('kind','reference'),int(value.get('urgent',False)),key))
        self.init_workbench()
        self.init_reliability()
        for (blob,) in self.db.execute('SELECT payload FROM events WHERE length(payload)>0 ORDER BY at DESC LIMIT 5000').fetchall():
            value=json.loads(unseal(blob))
            if value.get('kind')=='group':self.remember_group(value['platform'],value['conversation'],value['label'])
        self.db.commit()
        # Crash during send is ambiguous, not permission to resend.
        with self.db:
            self.db.execute("UPDATE delivery SET status='uncertain' WHERE status='sending'")

    def store_matter(self, group, topic, origin='live'):
        summary=str(topic.get('summary','')).strip()[:500]
        if not summary:return None
        raw_time=topic.get('source_time') or max((self.message_time(m.get('time')) for m in group.get('messages',[])),default=time.time())
        day=datetime.fromtimestamp(raw_time,TZ).strftime('%Y-%m-%d')
        key=hashlib.sha256(json.dumps([group.get('platform'),group.get('source') or group['label'],day,re.sub(r'\W','',summary),topic.get('deadline','')],ensure_ascii=False).encode()).hexdigest()
        value={'platform':group.get('platform'),'source':group['label'],'conversation':group.get('source',''),'source_time':raw_time,'time_approximate':topic.get('time_approximate',True),'summary':summary,'deadline':str(topic.get('deadline',''))[:100],'action':str(topic.get('action',''))[:200],'kind':'action' if topic.get('urgent') or topic.get('important') or (topic.get('useful') and topic.get('action')) else 'reference','urgent':topic.get('urgent') is True,'origin':origin}
        with self.db:
            key,value,write=self.revise_matter(key,value)
            if not write:return key
            self.db.execute('INSERT INTO matters(id,payload,at,kind,urgent) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,kind=excluded.kind,urgent=excluded.urgent', (key,seal(json.dumps(value,ensure_ascii=False)),time.time(),value['kind'],int(value['urgent'])))
        return key

    @staticmethod
    def message_time(value):
        try:return float(value)
        except (TypeError,ValueError):
            try:return datetime.fromisoformat(str(value)).timestamp()
            except (TypeError,ValueError):return time.time()

    def refill_qq(self):
        capacity=500-self.db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]
        if capacity<=0:return
        rows=self.db.execute('SELECT a.id,a.payload,a.at FROM qq_archive a WHERE a.at>? AND NOT EXISTS(SELECT 1 FROM events e WHERE e.id=a.id) ORDER BY a.at LIMIT ?', (time.time()-86400,min(50,capacity))).fetchall()
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO events(id,payload,at,status) VALUES(?,?,?,'pending')",rows)

    def operations_data(self):
        matters=[]
        for key,blob,at,done,starred,snoozed in self.db.execute('SELECT id,payload,at,done,starred,snoozed FROM matters WHERE at>? OR starred=1 ORDER BY done,kind,urgent DESC,at DESC LIMIT 300',(time.time()-30*86400,)):
            value=json.loads(unseal(blob));value.update(id=key,at=at,done=bool(done),starred=bool(starred),snoozed=bool(snoozed));matters.append(value)
        failures=[]
        for key,blob,at,retries in self.db.execute("SELECT id,payload,at,retries FROM events WHERE status='analysis_failed' ORDER BY at DESC LIMIT 30"):
            value=json.loads(unseal(blob))
            failures.append({'id':key,'source':value['label'],'platform':value['platform'],'at':at,'retries':retries})
        uncertain=[{'id':key,'at':at} for key,at in self.db.execute("SELECT id,at FROM delivery WHERE status='uncertain' ORDER BY at DESC LIMIT 30")]
        count,tokens=self.db.execute("SELECT count(*),coalesce(sum(tokens),0) FROM token_ledger WHERE status='reserved' AND at<?",(time.time()-180,)).fetchone()
        overflow=self.db.execute('SELECT count(*) FROM qq_archive a WHERE at>? AND NOT EXISTS(SELECT 1 FROM events e WHERE e.id=a.id)',(time.time()-86400,)).fetchone()[0]
        groups=[]
        for key,blob,focused,level,delivery,last_seen in self.db.execute('SELECT id,payload,focused,level,delivery,last_seen FROM watched_groups'):
            group=json.loads(unseal(blob));group.update(id=key,focused=bool(focused),level=level,delivery=delivery,last_seen=last_seen);groups.append(group)
        return {'preferences':self.preferences,'groups':groups,'matters':matters,'links':[{'child':c,'parent':p} for c,p in self.db.execute('SELECT child,parent FROM matter_links')],'failures':failures,'uncertain':uncertain,'usage_unverified':{'requests':count,'tokens':tokens},'qq_waiting_recovery':overflow,'retention':'事项保留30天；失败原消息最多24小时；QQ补处理范围为最近24小时',**self.reliability_data(),**self.plus_data(),'full_review':self.full_review_data()}

    async def operations(self,request):
        import hmac
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+self.secret['callback_token']):raise web.HTTPUnauthorized()
        if request.method=='GET':
            data=self.operations_data()
            if 'files_offset' in request.query:
                try:offset=int(request.query['files_offset'])
                except ValueError:raise web.HTTPBadRequest(text='invalid offset')
                if not 0<=offset<=10000000:raise web.HTTPBadRequest(text='invalid offset')
                try:data['full_review']=self.full_review_data(offset,{k:request.query.get(k,'') for k in ('q','platform','start','end')})
                except ValueError:raise web.HTTPBadRequest(text='invalid filter')
            return web.json_response(data)
        value=await request.json();action=value.get('action');key=value.get('id')
        if self.plus_action(value):return web.json_response({'ok':True})
        if self.full_review_action(value):return web.json_response({'ok':True})
        if self.reliability_action(value):return web.json_response({'ok':True})
        if self.workbench_action(value):return web.json_response({'ok':True})
        with self.db:
            if action=='focus_group' and isinstance(key,str) and isinstance(value.get('focused'),bool):
                self.db.execute('UPDATE watched_groups SET focused=?,level=? WHERE id=?',(int(value['focused']),'重点关注' if value['focused'] else '普通关注',key))
            elif action=='done' and isinstance(key,str):
                self.db.execute('UPDATE matters SET done=? WHERE id=?',(1 if value.get('done',True) else 0,key))
                if value.get('done',True):self.cancel_reminders(key)
            elif action=='retry_analysis' and isinstance(key,str):
                if self.db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]>=500:return web.json_response({'message':'队列已满，请稍后重试。'},status=409)
                self.db.execute("UPDATE events SET status='pending',retries=0,next_attempt=0 WHERE id=? AND status='analysis_failed'",(key,))
            elif action=='retry_delivery' and isinstance(key,str) and value.get('confirmed') is True:
                self.db.execute("UPDATE delivery SET status='pending' WHERE id=? AND status='uncertain'",(key,))
            elif action=='dismiss_delivery' and isinstance(key,str):
                self.db.execute("UPDATE delivery SET status='confirmed',payload='' WHERE id=? AND status='uncertain'",(key,))
                self.db.execute("UPDATE delivery_meta SET reason='用户确认已收到',updated=? WHERE id=?",(time.time(),key))
            else:raise web.HTTPBadRequest()
        return web.json_response({'ok':True})
