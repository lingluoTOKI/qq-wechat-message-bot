"""Separate operational chatter from personal knowledge without deleting archives."""
import json
import math
import re
import time
from contextvars import ContextVar

QUERY=ContextVar('dafeiyu_memory_query',default='')

TECH=r'(?:机器人|插件|日志|缓存|预算|账单|配置|接口|服务器|模型|token|API|AstrBot|QQ适配|上下文|记忆系统|人物画像|AI助手|托盘)'
ACTION=r'(?:读取|查看|检查|修复|优化|修改|改成|改为|配置|部署|重启|调试|测试|验收|报错|排查|上线|调用|接入|生效|上传|备份|清理|编写|整合(?!包)|加了.{0,6}功能|制作.{0,6}功能)'
OPERATIONS=re.compile(ACTION+r'.{0,28}'+TECH+'|'+TECH+r'.{0,28}'+ACTION,re.I)
INFERRED=re.compile(r'(?:爱折腾|热衷|痴迷|技术宅|技术控|极宠溺|依赖AI|爱装插件|喜欢给.{0,8}项目装.{0,10}插件)',re.I)
SECRET=re.compile(r'(?:ntn_|sk-)[A-Za-z0-9_-]{15,}|-----BEGIN .*PRIVATE KEY|(?:密码|密钥|token)\s*[:：=]\s*\S{6,}',re.I)
INSTRUCTION=re.compile(r'忽略.{0,8}(?:指令|规则|提示)|(?:系统提示词|system prompt)\s*[:：]|你现在必须|把.{0,8}写入.{0,6}(?:画像|记忆)',re.I)
COMMAND=re.compile(r'^[/／](?!点歌\s*|搜索歌曲\s*|翻唱\s*)\S+',re.I)
DIAGNOSTIC=re.compile(r'```|Traceback \(most recent call last\)|\[?(?:CacheGuard|ContextBudget|TokenBudget)|^\s*[{\[].*["\'](?:usage|tokens|error|model)["\']',re.I|re.S)


def plain(text):
    text=re.sub(r'\[CQ:[^\]]*\]|\[At:[^\]]*\]|<@!?[0-9A-Fa-f]+>','',str(text or ''))
    # A quoted speaker's assertion is not the author's self-report.
    return re.sub(r'\[引用[:：].*?\]','',text,flags=re.S).strip()


def reason(text, *, summary=False, category=None):
    text=plain(text)
    if SECRET.search(text):return '疑似凭据，不进入人物知识'
    if INSTRUCTION.search(text):return '资料中的指令，不是人物事实'
    if COMMAND.search(text) or DIAGNOSTIC.search(text):return '命令或调试输出'
    if OPERATIONS.search(text):return '临时调试或运维操作'
    if category=='身份' and re.search(r'问过|询问|会不会',text):return '单次提问不是身份信息'
    if category=='雷点' and re.search(r'机器人.{0,15}(?:字数|输出|达标)',text):return '输出验收要求不是人物雷点'
    if re.search(TECH,text,re.I) and INFERRED.search(text):return '由技术互动推断的性格或兴趣，待核实'
    if summary and re.search(r'极宠溺|依赖AI|情绪不稳定|讨好型人格|好奇心强|爱追问|爱找bot测试',text,re.I):return '缺乏本人确认的心理标签'
    return ''


def clean_summary(text):
    parts=[]
    for part in re.split(r'[，。；\n]',str(text or '')):
        part=part.strip()
        if not part:continue
        if reason(part,summary=True):
            # Preserve an unrelated, factual suffix in a mixed legacy clause.
            suffix=part.rsplit('的',1)[-1] if '的' in part else ''
            if suffix and suffix!=part and not reason(suffix,summary=True) and re.search(r'音游|动漫|二次元|玩家|学生',suffix):
                parts.append(suffix)
            continue
        parts.append(part)
    return '，'.join(parts)


def select_facts(rows, query='', limit=6, now=None):
    """Automatic use is selective; raw archives remain separately searchable."""
    now=time.time() if now is None else now
    personal=bool(re.search(r'我是谁|记得我|我的(?:画像|爱好|生日)|介绍.{0,3}我|了解我',query or ''))
    grams={query[i:i+2] for i in range(max(0,len(query)-1)) if re.fullmatch(r'[\w\u4e00-\u9fff]{2}',query[i:i+2])}
    ranked=[];seen=set()
    for row in rows:
        fact=str(row['fact'] or '')
        if reason(fact,category=row['category']) or fact in seen:continue
        seen.add(fact)
        confidence=float(row['confidence'] or 0)
        if not math.isfinite(confidence) or confidence<.75:continue
        keys=row.keys()
        if row['category']=='经历' and 'created_at' in keys and now-float(row['created_at'] or 0)>90*86400:continue
        overlap=sum(g in fact for g in grams)
        if query and not personal and overlap==0:continue
        ranked.append((overlap,confidence,row))
    ranked.sort(key=lambda value:(-value[0],-value[1]))
    return [row for _,_,row in ranked[:limit]]


def evidence(item, owned):
    """Only literal quotes from eligible messages by this identity qualify."""
    result=[]
    refs=item.get('evidence')
    if not isinstance(refs,list):return []
    for ref in refs[:4]:
        if not isinstance(ref,dict):continue
        identity=ref.get('id');quote=str(ref.get('quote') or '').strip()
        if type(identity) is not int or identity not in owned or not 2<=len(quote)<=160:continue
        if quote not in plain(owned[identity]['content']):continue
        if reason(quote):continue
        if identity not in [r['id'] for r in result]:result.append({'id':identity,'quote':quote})
    return result


def repair(store, apply=False):
    """Quarantine clear pollution and version summary edits. No raw rows deleted."""
    facts=store.db.execute('SELECT * FROM facts WHERE superseded_by IS NULL').fetchall()
    people=store.db.execute("SELECT * FROM people WHERE coalesce(summary,'')<>''").fetchall()
    flagged=[(row,reason(row['fact'],category=row['category'])) for row in facts if reason(row['fact'],category=row['category'])]
    edits=[(row,clean_summary(row['summary'])) for row in people if clean_summary(row['summary'])!=row['summary']]
    if apply:
        for row,why in flagged:
            store.db.execute('INSERT OR IGNORE INTO fact_quality VALUES(?,?,?,?)',(row['id'],'quarantined',why,time.time()))
        for row,new in edits:
            store.db.execute('INSERT INTO profile_revisions(sender_id,old_summary,new_summary,reason,created_at) VALUES(?,?,?,?,?)',
                             (row['sender_id'],row['summary'],new,'清理调试推断与无依据标签',time.time()))
            store.db.execute('UPDATE people SET summary=?,updated_at=? WHERE sender_id=?',(new,time.time(),row['sender_id']))
        store.commit()
    return {'people_scanned':len(people),'active_facts_scanned':len(facts),'facts_quarantined':len(flagged),
            'summaries_cleaned':len(edits),'reasons':list(dict.fromkeys(why for _,why in flagged)),
            'raw_events_deleted':0,'applied':apply}


def social_memory(text):
    return clean_summary(text).replace('，','；')


def source_note(row):
    try:
        ref=json.loads(row['source_ref'] or '{}')
        if isinstance(ref,dict) and ref.get('basis')=='user_audit_feedback':
            return '审核保留的兴趣/互动线索·原消息待核对'
        if isinstance(ref,dict) and ref.get('evidence_ids'):
            times=ref.get('source_times') or []
            when=time.strftime('%m-%d',time.gmtime(max(times)+8*3600)) if times else ''
            return '本人原话依据'+('·'+when if when else '')
    except (ValueError,TypeError,KeyError):pass
    return '历史记录·来源待核对'


def bot_context_note(context):
    if not isinstance(context,dict) or not context.get('bot_name'):return ''
    return ('机器人指代说明（用户确认）：此人的相关对话中的机器人是其自行部署的「'
            +str(context['bot_name'])+'」，与当前小洛是不同实例。'
            '不可把与该机器人的互动当成与小洛的共同经历，也不可把该机器人的自述归给此人。')
