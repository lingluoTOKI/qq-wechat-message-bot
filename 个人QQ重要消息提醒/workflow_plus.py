"""Scoped archive jobs, source preferences, explicit corrections and file audit."""
import asyncio,hashlib,json,math,re,time,uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from aiohttp import web
from vault import seal,unseal
from reference_library import library_data,save_metadata

def packed(value):return seal(json.dumps(value,ensure_ascii=False))
def fingerprint(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def signature(text):return re.sub(r'\W','',str(text)).casefold()

class WorkflowPlus:
    def init_plus(self):
        self.db.executescript('''CREATE TABLE IF NOT EXISTS source_preferences(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS matter_feedback(id TEXT PRIMARY KEY,payload TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS reference_metadata(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS notice_context(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS file_audit(id TEXT PRIMARY KEY,file_id TEXT,payload TEXT,status TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS content_uploads(hash TEXT,target TEXT,status TEXT,receipt TEXT,PRIMARY KEY(hash,target));''')
        self.db.execute('CREATE INDEX IF NOT EXISTS file_audit_at ON file_audit(at)')
        self.db.execute('CREATE INDEX IF NOT EXISTS file_candidates_at ON file_candidates(at)')
        self.db.execute('CREATE INDEX IF NOT EXISTS matter_feedback_at ON matter_feedback(at)')
        self.db.execute("UPDATE file_audit SET status='uncertain' WHERE status IN ('sending','preparing')")
        self.preview_task=None;self.file_check_task=None
        self._source_context_cache={}
        self.scan_preview={'state':'idle','sources':[]}
        self.db.commit()

    def source_key(self,g):return str(g.get('platform'))+':'+str(g.get('source') or g.get('conversation',''))

    def source_context(self,g):
        key=self.source_key(g)
        if key in self._source_context_cache:return self._source_context_cache[key]
        row=self.db.execute('SELECT payload FROM source_preferences WHERE id=?',(key,)).fetchone()
        rules=json.loads(unseal(row[0])) if row else {'focus':''}
        feedback=[]
        for blob, in self.db.execute('SELECT payload FROM matter_feedback ORDER BY at DESC LIMIT 500'):
            v=json.loads(unseal(blob))
            if v['source_key']==key:
                feedback.append({'summary':v['summary'][:120],'choice':v['choice']})
                if len(feedback)>=8:break
        result=dict(focus=rules.get('focus',''),feedback=feedback)
        self._source_context_cache[key]=result
        return result

    def apply_topic_feedback(self,g,t):
        for v in self.source_context(g)['feedback']:
            if signature(v['summary'])==signature(t.get('summary','')):
                if v['choice'] in ('irrelevant','duplicate'):t.update(relevant=False,urgent=False,important=False,useful=False,reference=False)
                elif v['choice']=='important':t.update(relevant=True,important=True)
                break
        return t

    def apply_result_feedback(self,result,groups):
        byid={g['id']:g for g in groups}
        for item in result.get('items',[]):
            if item.get('id') not in byid:continue
            for topic in item.get('topics',[]):self.apply_topic_feedback(byid[item['id']],topic)
            item['relevant']=any(t.get('relevant') for t in item.get('topics',[]))
        return result

    def validate_scope(self,raw):
        if not isinstance(raw,dict):raise web.HTTPBadRequest(text='请选择扫描范围')
        p=raw.get('platform','all');kind=raw.get('kind','all');selection=raw.get('selection','focused');mode=raw.get('mode','history')
        if p not in ('all','qq','wechat') or kind not in ('all','group','private') or selection not in ('focused','selected','all') or mode not in ('history','files'):raise web.HTTPBadRequest(text='扫描范围无效')
        selected=raw.get('selected',[])
        if not isinstance(selected,list) or len(selected)>200 or any(not isinstance(v,str) or len(v)>200 for v in selected):raise web.HTTPBadRequest(text='最多选择200个会话')
        if selection=='selected' and not selected:raise web.HTTPBadRequest(text='请至少选择一个会话')
        bounds=[]
        for field in ('start','end'):
            value=raw.get(field,0)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0 or value>4102444800:raise web.HTTPBadRequest(text='日期范围无效')
            bounds.append(value)
        if bounds[1] and bounds[0]>bounds[1]:raise web.HTTPBadRequest(text='开始日期不能晚于结束日期')
        return dict(platform=p,kind=kind,selection=selection,selected=selected,mode=mode,start=bounds[0],end=bounds[1] or time.time())

    def select_sources(self,sources,scope):
        focused={k for k, in self.db.execute("SELECT id FROM watched_groups WHERE focused=1")}
        def include(g):
            key=g['platform']+':'+g['kind']+':'+g['source']
            return (scope['platform']=='all' or g['platform']==scope['platform']) and (scope['kind']=='all' or g['kind']==scope['kind']) and (scope['selection']=='all' or scope['selection']=='selected' and key in scope['selected'] or scope['selection']=='focused' and self.source_key(g) in focused)
        return [g for g in sources if include(g)]

    def in_scan_range(self,stamp):
        scope=self.full_job.get('scope',{})
        return scope.get('start',0)<=stamp<=scope.get('end',time.time())

    async def preview_scan(self,scope):
        self.scan_preview={'state':'loading','sources':[],'scope':scope}
        try:
            sources,errors=await self.discover_sources(scope['platform'],scope['kind'])
            selected=self.select_sources(sources,scope)
            self.scan_preview=dict(state='ready',sources=[dict(g,id=g['platform']+':'+g['kind']+':'+g['source']) for g in sources],selected_count=len(selected),errors=errors,scope=scope,at=time.time(),message='会话列表已读取；消息总量未知，开始后逐页统计。')
        except Exception as error:self.scan_preview=dict(state='error',sources=[],message='列表读取失败：'+type(error).__name__)

    def plus_action(self,value):
        action=value.get('action');key=value.get('id')
        if action=='reference_metadata':return save_metadata(self.db,value)
        if action=='scan_preview':
            if self.preview_task and not self.preview_task.done():raise web.HTTPConflict(text='正在读取会话列表')
            scope=self.validate_scope(value.get('scope',{}));self.preview_task=asyncio.create_task(self.preview_scan(scope));return True
        if action in ('full_skip','full_stop','full_retry'):
            job=self.full_job;running=self.full_task and not self.full_task.done()
            if action=='full_retry':
                if running:raise web.HTTPConflict(text='请先暂停当前任务')
                failed=job.get('failed_sources',[])
                if not failed:raise web.HTTPConflict(text='没有可重试的失败会话')
                # Retry failed sources, then finish any untouched portion of a paused job.
                retry=[];seen=set()
                for source in failed+job.get('sources',[])[job.get('source_index',0):]:
                    identity=(source['platform'],source['kind'],source['source'])
                    if identity not in seen:seen.add(identity);retry.append(source)
                job.update(sources=retry,source_index=0,total_sources=len(retry),failed_sources=[],errors=[],offset=0,anchor=0,page_keys=None,state='paused',stop_requested=False,skip_index=None,pause_requested=False)
                return self.full_review_action({'action':'full_resume'})
            if action=='full_stop':
                if running:job['stop_requested']=True;job['stage']='当前请求结束后停止'
                else:job.update(state='cancelled',stage='任务已结束，可新建扫描')
            elif running:job['skip_index']=job['source_index'];job['stage']='当前请求结束后跳过此会话'
            elif job.get('state')=='paused' and job.get('sources'):
                job['source_index']+=1;job.update(offset=0,anchor=0,page_keys=None,stage='已跳过，点击继续')
            else:raise web.HTTPConflict(text='没有可跳过的会话')
            self.save_full_job();return True
        if action=='source_focus':
            if not self.db.execute('SELECT 1 FROM watched_groups WHERE id=?',(key,)).fetchone():raise web.HTTPNotFound()
            focus=value.get('focus')
            if not isinstance(focus,str) or len(focus)>500:raise web.HTTPBadRequest(text='关注内容最多500字')
            with self.db:self.db.execute('INSERT OR REPLACE INTO source_preferences VALUES(?,?)',(key,packed({'focus':focus.strip()})))
            self._source_context_cache.pop(key,None)
            return True
        if action=='matter_feedback':
            row=self.db.execute('SELECT payload,done,starred FROM matters WHERE id=?',(key,)).fetchone()
            if not row:raise web.HTTPNotFound()
            v=json.loads(unseal(row[0]));choice=value.get('choice')
            if choice not in ('important','irrelevant','duplicate','undo'):raise web.HTTPBadRequest()
            old=self.db.execute('SELECT payload FROM matter_feedback WHERE id=?',(key,)).fetchone()
            prior=json.loads(unseal(old[0])) if old else None
            with self.db:
                if choice=='undo':
                    if not prior:raise web.HTTPConflict(text='没有反馈可撤销')
                    original=prior['original'];self.db.execute('UPDATE matters SET payload=?,kind=?,urgent=?,done=?,starred=? WHERE id=?',(packed(original),original['kind'],int(original.get('urgent',False)),prior['done'],prior['starred'],key));self.db.execute('DELETE FROM matter_feedback WHERE id=?',(key,))
                else:
                    duplicate=value.get('duplicate_of','')
                    if choice=='duplicate' and (not duplicate or duplicate==key or not self.db.execute('SELECT 1 FROM matters WHERE id=?',(duplicate,)).fetchone()):raise web.HTTPBadRequest(text='请选择重复对应的另一条事项')
                    record=dict(source_key=v['platform']+':'+v['conversation'],summary=v['summary'],choice=choice,duplicate_of=duplicate,original=prior['original'] if prior else v,done=prior['done'] if prior else row[1],starred=prior['starred'] if prior else row[2])
                    self.db.execute('INSERT OR REPLACE INTO matter_feedback VALUES(?,?,?)',(key,packed(record),time.time()))
                    if choice=='important':
                        updated=dict(v,kind='action',urgent=False,action=v.get('action') or '已手动标为重要，请查看原会话')
                        self.db.execute('UPDATE matters SET payload=?,kind=?,urgent=0,done=0,starred=1 WHERE id=?',(packed(updated),'action',key))
                    else:
                        self.db.execute('UPDATE matters SET done=1 WHERE id=?',(key,));self.cancel_reminders(key)
                        for table in ('delivery','digest'):
                            self.db.execute("UPDATE "+table+" SET status='cancelled',payload='' WHERE status='pending' AND (id=? OR id LIKE ?)",('matter:'+key,'matter:'+key+':v%'))
            self._source_context_cache.pop(v['platform']+':'+v['conversation'],None)
            return True
        if action=='deadline_reminder':
            stamp=value.get('deadline');lead=value.get('lead')
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or lead not in (3600,86400):raise web.HTTPBadRequest(text='请确认期限并选择提前量')
            self.reliability_action(dict(action='schedule_reminder',id=key,due=stamp-lead))
            row=self.db.execute('SELECT payload FROM matters WHERE id=?',(key,)).fetchone();v=json.loads(unseal(row[0]));v['confirmed_deadline']=stamp
            with self.db:self.db.execute('UPDATE matters SET payload=? WHERE id=?',(packed(v),key))
            return True
        if action=='file_check':
            ids=value.get('files')
            if not isinstance(ids,list) or not 1<=len(ids)<=10 or any(not isinstance(i,str) for i in ids):raise web.HTTPBadRequest(text='请选择1至10个文件')
            if self.file_check_task and not self.file_check_task.done():raise web.HTTPConflict(text='正在检查内容指纹')
            self.file_check_task=asyncio.create_task(self.check_files(ids));return True
        if action=='file_resolve':
            row=self.db.execute('SELECT payload,status FROM file_candidates WHERE id=?',(key,)).fetchone()
            if not row or row[1]!='uncertain':raise web.HTTPConflict(text='仅处理上传待确认文件')
            v=json.loads(unseal(row[0]));choice=value.get('choice')
            if choice not in ('received','retry'):raise web.HTTPBadRequest()
            state='confirmed' if choice=='received' else 'failed'
            with self.db:
                v['reason']='用户确认已在群文件中找到' if choice=='received' else '用户确认未收到，可手动重试'
                self.db.execute('UPDATE file_candidates SET payload=?,status=? WHERE id=?',(packed(v),state,key))
                for aid,blob in self.db.execute("SELECT id,payload FROM file_audit WHERE file_id=? AND status='uncertain'",(key,)).fetchall():
                    audit=json.loads(unseal(blob));audit.update(reason=v['reason'],updated=time.time())
                    self.db.execute('UPDATE file_audit SET payload=?,status=? WHERE id=?',(packed(audit),state,aid))
                if v.get('content_hash'):self.db.execute('UPDATE content_uploads SET status=? WHERE hash=? AND target=?',(state,v['content_hash'],str(v.get('upload_target',''))))
            return True
        return False

    async def check_files(self,ids):
        for key in ids:
            row=self.db.execute('SELECT payload FROM file_candidates WHERE id=?',(key,)).fetchone()
            if not row:continue
            v=json.loads(unseal(row[0]))
            try:
                path=Path(v.get('path','')).resolve();base=Path(v.get('base','')).resolve()
                if v.get('platform')!='wechat' or not path.is_file() or base not in path.parents:raise ValueError('not local')
                stat=path.stat();h=await asyncio.to_thread(fingerprint,path)
                if (path.stat().st_size,path.stat().st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):raise ValueError('changed')
                v.update(content_hash=h,hash_size=stat.st_size,hash_mtime=stat.st_mtime_ns,hash_note='本机文件内容指纹已检查')
            except Exception:v['hash_note']='未找到稳定的本机原文件，不能做内容去重；QQ远程附件仅按文件标识去重'
            latest=self.db.execute('SELECT payload FROM file_candidates WHERE id=?',(key,)).fetchone()
            if latest:
                current=json.loads(unseal(latest[0]));current.update({k:v[k] for k in ('content_hash','hash_size','hash_mtime','hash_note') if k in v})
                with self.db:self.db.execute('UPDATE file_candidates SET payload=? WHERE id=?',(packed(current),key))

    def plus_data(self):
        focus={k:json.loads(unseal(p)) for k,p in self.db.execute('SELECT id,payload FROM source_preferences')}
        feedback={k:{field:v.get(field) for field in ('choice','duplicate_of')} for k,p in self.db.execute('SELECT id,payload FROM matter_feedback') for v in [json.loads(unseal(p))]}
        records=[]
        for key,fileid,blob,state,at in self.db.execute('SELECT id,file_id,payload,status,at FROM file_audit ORDER BY at DESC LIMIT 100'):
            records.append(dict(json.loads(unseal(blob)),id=key,file_id=fileid,status=state,at=at))
        descriptions={k:json.loads(unseal(p)) for k,p in self.db.execute('SELECT n.id,n.payload FROM notice_context n JOIN delivery d ON d.id=n.id ORDER BY d.at DESC LIMIT 100')}
        for key,blob in self.db.execute("SELECT id,payload FROM delivery WHERE status IN ('pending','failed','uncertain') AND length(payload)>0 ORDER BY at DESC LIMIT 100"):
            if key not in descriptions:
                try:descriptions[key]={'summary':'\n'.join(s.get('data',{}).get('text','') for s in json.loads(unseal(blob)).get('params',{}).get('message',[]))[:600]}
                except Exception:pass
        return dict(reference_library=library_data(self.db),scan_preview=self.scan_preview,source_preferences=focus,feedback=feedback,file_deliveries=records,delivery_details=descriptions,file_check_running=bool(self.file_check_task and not self.file_check_task.done()))
