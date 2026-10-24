"""Encrypted workbench preferences and additive metadata migrations."""
import json
import re
from vault import seal, unseal
from aiohttp import web

INTERESTS=['考研报名与考试','招生政策','班级通知','材料提交','评奖评优','委员工作','课程与考试','活动报名','实习就业']
LEVELS=('重点关注','普通关注','低关注','暂停分析')

class WorkbenchStore:
    def init_workbench(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS workbench_settings(id TEXT PRIMARY KEY,payload TEXT)')
        for table, definitions in [('watched_groups',{'level':"TEXT NOT NULL DEFAULT '普通关注'",'delivery':"TEXT NOT NULL DEFAULT '重要事项即时'",'last_seen':'REAL NOT NULL DEFAULT 0'}),('matters',{'starred':'INTEGER NOT NULL DEFAULT 0','snoozed':'INTEGER NOT NULL DEFAULT 0'})]:
            columns={r[1] for r in self.db.execute('PRAGMA table_info('+table+')')}
            for name,definition in definitions.items():
                if name not in columns:
                    self.db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
                    if table=='watched_groups' and name=='level':
                        self.db.execute("UPDATE watched_groups SET level='重点关注' WHERE focused=1")
        row=self.db.execute("SELECT payload FROM workbench_settings WHERE id='preferences'").fetchone()
        self.preferences=json.loads(unseal(row[0])) if row else self.default_preferences()
        self.preferences.setdefault('file_group',str(self.cfg.get('target_group','917905372')))
        if row:self.apply_preferences()
        self.db.commit()

    def default_preferences(self):
        return dict(name='欧阳宸',aliases='欧阳宸',school='池州学院',className='计算机科学与技术2班',role='思政委员',goal='正在备考研究生',target='',description='优先关注与本人有关的考研重要信息、班群通知及思政委员工作。',interests=['考研报名与考试','班级通知','材料提交','评奖评优','委员工作'],uncertain='保留为待确认',timing='重要事项即时，其余每日汇总',digest=f"{self.cfg.get('digest_hour',21):02d}:{self.cfg.get('digest_minute',0):02d}",recipient=str(self.cfg.get('target_user','474496195')),noise=True,merge=True)

    def apply_preferences(self):
        p=self.preferences
        self.cfg['profile']={k:v for k,v in p.items() if k not in ('recipient','file_group','digest','timing')}
        self.cfg['target_user']=p['recipient']
        self.cfg['target_group']=p.get('file_group',str(self.cfg.get('target_group','917905372')))
        self.cfg['digest_hour'],self.cfg['digest_minute']=map(int,p['digest'].split(':'))

    def validate_preferences(self,p):
        if not isinstance(p,dict):raise web.HTTPBadRequest(text='invalid_preferences')
        result={}
        for key in ('name','aliases','school','className','role','goal','target','description'):
            value=p.get(key,'')
            if not isinstance(value,str) or len(value)>(1000 if key=='description' else 400):raise web.HTTPBadRequest(text='invalid_profile')
            result[key]=value.strip()
        if not isinstance(p.get('recipient'),str) or not re.fullmatch(r'\d{5,12}',p['recipient']):raise web.HTTPBadRequest(text='invalid_recipient')
        if not isinstance(p.get('digest'),str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',p['digest']):raise web.HTTPBadRequest(text='invalid_time')
        for key,choices in [('uncertain',('保留为待确认','仅放参考资料')),('timing',('重要事项即时，其余每日汇总','全部仅每日汇总'))]:
            if p.get(key) not in choices:raise web.HTTPBadRequest(text='invalid_preference')
            result[key]=p[key]
        if not isinstance(p.get('interests'),list) or len(p['interests'])>len(INTERESTS) or any(v not in INTERESTS for v in p['interests']):raise web.HTTPBadRequest(text='invalid_interests')
        for key in ('noise','merge'):
            if type(p.get(key)) is not bool:raise web.HTTPBadRequest(text='invalid_boolean')
            result[key]=p[key]
        result.update(recipient=p['recipient'],digest=p['digest'],interests=list(dict.fromkeys(p['interests'])))
        result['file_group']=self.preferences.get('file_group',str(self.cfg.get('target_group','917905372')))
        return result

    def group_policy(self,value):
        source=value.get('conversation') or value.get('source')
        if value.get('kind')!='group':return ('普通关注','重要事项即时')
        row=self.db.execute('SELECT level,delivery FROM watched_groups WHERE id=?',(value['platform']+':'+str(source),)).fetchone()
        return tuple(row) if row else ('普通关注','重要事项即时')

    def allow_immediate(self,group):
        return self.preferences.get('timing')!='全部仅每日汇总' and self.group_policy(group)[1]!='仅每日汇总'

    def workbench_action(self,value):
        action=value.get('action');key=value.get('id')
        if action=='notification_targets':
            targets={}
            for field in ('file_group','recipient'):
                raw=value.get(field)
                if not isinstance(raw,str) or not re.fullmatch(r'[1-9][0-9]{4,11}',raw.strip()):
                    raise web.HTTPBadRequest(text='请输入5至12位有效QQ号或群号')
                targets[field]=raw.strip()
            if targets['recipient']==str(self.cfg.get('qq_account')):raise web.HTTPBadRequest(text='接收人不能是当前发送账号')
            p=dict(self.preferences,**targets)
            with self.db:self.db.execute("INSERT OR REPLACE INTO workbench_settings VALUES('preferences',?)",(seal(json.dumps(p,ensure_ascii=False)),))
            self.preferences=p
            self.cfg.update(target_user=p['recipient'],target_group=p['file_group'])
            return True
        if action=='preferences':
            if getattr(self,'lock',None) and self.lock.locked():
                raise web.HTTPConflict(text='正在分析，请本批完成后再保存偏好')
            p=self.validate_preferences(value.get('preferences'))
            with self.db:self.db.execute("INSERT OR REPLACE INTO workbench_settings VALUES('preferences',?)",(seal(json.dumps(p,ensure_ascii=False)),))
            self.preferences=p;self.apply_preferences();return True
        if action=='batch-tuning':
            bs=value.get('batch_seconds');qs=value.get('quiet_seconds')
            if not(isinstance(bs,int) and 30<=bs<=600 and isinstance(qs,int) and 30<=qs<=600):
                raise web.HTTPBadRequest(text='参数范围30~600秒')
            self.cfg['batch_seconds']=bs;self.cfg['quiet_seconds']=qs
            cfg_path=Path(self.root)/'config.json'
            cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
            cfg['batch_seconds']=bs;cfg['quiet_seconds']=qs
            cfg_path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
            return True
        if action=='group_settings':            if value.get('level') not in LEVELS or value.get('delivery') not in ('重要事项即时','仅每日汇总'):raise web.HTTPBadRequest(text='invalid_group_setting')
            if not self.db.execute('SELECT 1 FROM watched_groups WHERE id=?',(key,)).fetchone():raise web.HTTPNotFound()
            with self.db:self.db.execute('UPDATE watched_groups SET level=?,delivery=?,focused=? WHERE id=?',(value['level'],value['delivery'],int(value['level']=='重点关注'),key))
            return True
        if action in ('star','snooze'):
            field='starred' if action=='star' else 'snoozed'
            if type(value.get('enabled')) is not bool:raise web.HTTPBadRequest()
            if not self.db.execute('SELECT 1 FROM matters WHERE id=?',(key,)).fetchone():raise web.HTTPNotFound()
            with self.db:self.db.execute('UPDATE matters SET '+field+'=? WHERE id=?',(int(value['enabled']),key))
            return True
        return False
