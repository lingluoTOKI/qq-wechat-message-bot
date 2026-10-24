"""Local lifecycle, conservative revisions, reminders and encrypted DB backups."""
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from aiohttp import web
from vault import seal, unseal
from full_review import FullReviewStore

TZ=ZoneInfo('Asia/Shanghai')

class DeliveryRejected(RuntimeError):
    """The gateway explicitly rejected this request; safe to retry finitely."""

def deadline_ts(text, source_time, approximate=False):
    text=str(text or '')
    match=re.search(r'(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2})[:：](\d{2})',text)
    try:
        if match:return datetime(*map(int,match.groups()),tzinfo=TZ).timestamp()
        match=re.fullmatch(r'(今天|今日|明天|明日)\s*(\d{1,2})[:：](\d{2})',text.strip())
        if match and not approximate:
            from datetime import timedelta
            day=datetime.fromtimestamp(source_time,TZ)+timedelta(days=int(match[1].startswith('明')))
            return day.replace(hour=int(match[2]),minute=int(match[3]),second=0,microsecond=0).timestamp()
    except (ValueError,TypeError,OverflowError):pass
    return None

class ReliabilityStore(FullReviewStore):
    def token_monitor(self,now=None):
        from datetime import timedelta
        now=time.time() if now is None else now
        today=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        days=[]
        for offset in range(6,-1,-1):
            day=today-timedelta(days=offset);start=day.timestamp();end=start+86400
            measured,requests,reserved=self.db.execute("SELECT coalesce(sum(CASE WHEN status='measured' THEN tokens ELSE 0 END),0),sum(CASE WHEN status='measured' THEN 1 ELSE 0 END),coalesce(sum(CASE WHEN status='reserved' THEN tokens ELSE 0 END),0) FROM token_ledger WHERE at>=? AND at<?",(start,end)).fetchone()
            days.append(dict(day=day.date().isoformat(),measured=measured,requests=requests or 0,reserved=reserved))
        return dict(today=days[-1],days=days,week_measured=sum(d['measured'] for d in days),session_cache=dict(getattr(self,'cache_stats',{})),session_started=self.started_at,checked_at=now)

    def init_reliability(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS delivery_meta(id TEXT PRIMARY KEY, attempts INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0, updated REAL, reason TEXT DEFAULT '', receipt TEXT DEFAULT '', recipient TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY, matter TEXT, due REAL, state TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS matter_links(child TEXT PRIMARY KEY,parent TEXT,at REAL);
        ''')
        self.started_at=time.time()
        self.db.execute('CREATE INDEX IF NOT EXISTS token_ledger_at ON token_ledger(at)')
        self.last_backup_error=''
        self.db.commit()
        self.init_full_review()

    def revise_matter(self,key,value):
        row=self.db.execute('SELECT id,payload FROM matters WHERE id=?',(key,)).fetchone()
        # Only identical normalized summaries in the SAME conversation within 48h.
        # A date change can update such a notice; semantic guesses never merge it.
        if not row and self.preferences.get('merge',True):
            canonical=lambda s:re.sub(r'\W','',s).casefold()
            for oldkey,blob in self.db.execute('SELECT id,payload FROM matters WHERE at>? ORDER BY at DESC LIMIT 300',(time.time()-2*86400,)):
                old=json.loads(unseal(blob))
                same_day=datetime.fromtimestamp(old.get('source_time',0),TZ).date()==datetime.fromtimestamp(value['source_time'],TZ).date()
                if same_day and old.get('platform')==value['platform'] and old.get('conversation')==value['conversation'] and old.get('source')==value['source'] and old.get('kind')==value['kind'] and canonical(old['summary'])==canonical(value['summary']):
                    row=(oldkey,blob);break
        if row:
            key,blob=row;old=json.loads(unseal(blob))
            if value['source_time']<old.get('source_time',0):return key,old,False
            changed=any(value.get(k)!=old.get(k) for k in ('summary','deadline','action','kind'))
            value['revision']=old.get('revision',0)+int(changed)
            revisions=old.get('revisions',[])
            if changed:
                self.db.execute('UPDATE matters SET at=? WHERE id=?',(time.time(),key))
                revisions=(revisions+[{k:old.get(k) for k in ('summary','deadline','action','source','source_time')}])[-20:]
                # An older queued message/reminder must not announce the old deadline.
                for delivery_id, in self.db.execute("SELECT id FROM delivery WHERE status='pending' AND (id=? OR id LIKE ?)",('matter:'+key,'matter:'+key+':v%')).fetchall():
                    self.db.execute("UPDATE delivery SET status='superseded',payload='' WHERE id=?",(delivery_id,))
                self.db.execute("UPDATE digest SET status='superseded',payload='' WHERE status='pending' AND (id=? OR id LIKE ?)",('matter:'+key,'matter:'+key+':v%'))
                if old.get('deadline')!=value['deadline']:
                    self.cancel_reminders(key)
                    value['reminder_notice']='通知期限已更新，旧提醒已取消，请核实后重新设置。'
            else:
                if old.get('reminder_notice'):value['reminder_notice']=old['reminder_notice']
            value['revisions']=revisions
        else:value.update(revision=0,revisions=[])
        return key,value,True

    def delivery_key(self,matter):
        row=self.db.execute('SELECT payload FROM matters WHERE id=?',(matter,)).fetchone()
        revision=json.loads(unseal(row[0])).get('revision',0) if row else 0
        return 'matter:'+matter+(':v'+str(revision) if revision else '')

    def cancel_reminders(self,matter):
        for key, in self.db.execute("SELECT id FROM reminders WHERE matter=? AND state IN ('scheduled','queued')",(matter,)).fetchall():
            self.db.execute("UPDATE delivery SET status='cancelled',payload='' WHERE id=? AND status='pending'",('reminder:'+key,))
        self.db.execute("UPDATE reminders SET state='cancelled' WHERE matter=? AND state IN ('scheduled','queued')",(matter,))
        self.db.execute('UPDATE matters SET snoozed=0 WHERE id=?',(matter,))

    def queue_reminders(self):
        now=time.time()
        with self.db:
            for key,matter,due,blob,done in self.db.execute("SELECT r.id,r.matter,r.due,m.payload,m.done FROM reminders r JOIN matters m ON m.id=r.matter WHERE r.state='scheduled' AND r.due<=?",(now,)).fetchall():
                if done:self.cancel_reminders(matter);continue
                v=json.loads(unseal(blob))
                overdue='（电脑离线后补提醒）' if now-due>300 else ''
                text=f'【待办提醒】{overdue}\n{v["summary"]}\n来源：{v["platform"]} · {v["source"]}\n期限：{v.get("deadline") or "待确认"}'
                payload={'action':'send_private_msg','params':{'user_id':int(self.cfg['target_user']),'message':[{'type':'text','data':{'text':text}}]}}
                self.db.execute('INSERT OR IGNORE INTO delivery VALUES(?,?,?,?)',('reminder:'+key,seal(json.dumps(payload,ensure_ascii=False)),'pending',now))
                self.db.execute("UPDATE reminders SET state='queued' WHERE id=?",(key,))
                self.db.execute('UPDATE matters SET snoozed=0 WHERE id=?',(matter,))

    def backup_data(self):
        folder=Path(self.root)/'backups'
        files=sorted(folder.glob('state-*.sqlite'),reverse=True) if folder.exists() else []
        return {'files':[{'name':p.name,'bytes':p.stat().st_size,'at':p.stat().st_mtime} for p in files[:7]],'database_bytes':(Path(self.root)/'state.sqlite').stat().st_size,'backup_bytes':sum(p.stat().st_size for p in files),'error':self.last_backup_error,'retention':'每日自动备份，最近7份；普通事项30天，收藏长期；摘要30天/30份，QQ原文7天/5000条。'}

    def create_backup(self,force=False):
        folder=Path(self.root)/'backups';folder.mkdir(exist_ok=True)
        day=datetime.now(TZ).strftime('%Y%m%d')
        target=folder/('state-'+day+'.sqlite')
        if target.exists() and not force:return
        temporary=folder/('state-'+day+'.tmp')
        self.db.commit()
        try:
            with closing(sqlite3.connect(temporary)) as dest:
                self.db.backup(dest)
                if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('backup_check_failed')
            temporary.replace(target)
            for old in sorted(folder.glob('state-*.sqlite'),reverse=True)[7:]:old.unlink()
            self.last_backup_error=''
        except Exception as error:
            self.last_backup_error=type(error).__name__
            raise

    def reliability_data(self):
        notices=[]
        for key,state,at,attempts,next_at,updated,reason,recipient in self.db.execute('SELECT d.id,d.status,d.at,coalesce(m.attempts,0),coalesce(m.next_attempt,0),m.updated,m.reason,m.recipient FROM delivery d LEFT JOIN delivery_meta m ON m.id=d.id ORDER BY d.at DESC LIMIT 100'):
            notices.append(dict(id=key,status=state,at=at,attempts=attempts,next_attempt=next_at,updated=updated,reason=reason or '',recipient=recipient or '',kind='定时提醒' if key.startswith('reminder:') else '每日汇总' if key.startswith('digest:') else '事项通知'))
        return {'deliveries':notices,'reminders':[dict(id=k,matter=m,due=d,state=s) for k,m,d,s in self.db.execute("SELECT id,matter,due,state FROM reminders WHERE state IN ('scheduled','queued') ORDER BY due")],'storage':self.backup_data(),'checked_at':time.time()}

    def reliability_action(self,value):
        action=value.get('action');key=value.get('id')
        if action in ('link_matter','unlink_matter'):
            child=value.get('child')
            if not isinstance(child,str) or child==key:raise web.HTTPBadRequest()
            rows=dict(self.db.execute('SELECT id,payload FROM matters WHERE id IN (?,?)',(key,child)))
            if len(rows)!=2:raise web.HTTPNotFound()
            a,b=[json.loads(unseal(rows[i])) for i in (key,child)]
            if any(a.get(k)!=b.get(k) for k in ('platform','conversation','source')):raise web.HTTPBadRequest(text='只能关联同一会话的通知')
            with self.db:
                if action=='link_matter':
                    if self.db.execute('SELECT 1 FROM matter_links WHERE child=? OR parent=? OR child=?',(key,child,child)).fetchone():raise web.HTTPConflict(text='已有其他关联，请先解除')
                    self.db.execute('INSERT INTO matter_links VALUES(?,?,?)',(child,key,time.time()))
                else:self.db.execute('DELETE FROM matter_links WHERE child=? AND parent=?',(child,key))
            return True
        if action in ('schedule_reminder','cancel_reminder'):
            row=self.db.execute('SELECT done FROM matters WHERE id=?',(key,)).fetchone()
            if not row:raise web.HTTPNotFound()
            if action=='schedule_reminder':
                due=value.get('due')
                if row[0]:raise web.HTTPConflict(text='已完成事项不需要提醒')
                if type(due) not in (int,float) or not math.isfinite(due) or not time.time()+5<=due<=time.time()+90*86400:raise web.HTTPBadRequest(text='请选择未来90天内的提醒时间')
                reminder=hashlib.sha256((key+':'+str(time.time_ns())).encode()).hexdigest()
            with self.db:
                self.cancel_reminders(key)
                if action=='schedule_reminder':
                    self.db.execute('INSERT INTO reminders VALUES(?,?,?,?,?)',(reminder,key,due,'scheduled',time.time()))
                    self.db.execute('UPDATE matters SET snoozed=? WHERE id=?',(int(value.get('hide') is True),key))
            return True
        if action=='retry_delivery_failed':
            with self.db:
                if not self.db.execute("SELECT 1 FROM delivery WHERE id=? AND status='failed'",(key,)).fetchone():raise web.HTTPConflict(text='当前通知不属于明确失败')
                self.db.execute("UPDATE delivery SET status='pending' WHERE id=?",(key,))
                self.db.execute('UPDATE delivery_meta SET attempts=0,next_attempt=0 WHERE id=?',(key,))
            return True
        if action=='backup':self.create_backup(True);return True
        if action=='cleanup':
            self.create_backup(True)
            self.clean_saved_history()
            return True
        return False
