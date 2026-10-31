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
# 一次性事件：对「这个人是谁」没有长期信息量，只占注入配额
ONE_OFF=re.compile(r'(?:被|被人|让人)?(?:踢出|踢掉|移除)|风扇|余额|吓到|某次|这次|当天|今晚|昨晚|上次|临时|改口|被指出|突然|结果发现|输赢')
# 免责式画像：等于没写，还挤掉了真正有信息的一句话
META_SUMMARY=re.compile(r'(?:无明确个人信息|发言极少|仅偶尔插话|信息不足|资料不足|没有足够|无法确定|暂无可记录|资料有限)',re.I)
# 生日已经单独存储并单独注入，画像和事实里不再重复
BIRTH_CLAUSE=re.compile(r'^(?:生日|出生)[是为]?\s*\d')


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
    if summary and META_SUMMARY.search(text):return '无信息量的免责式画像'
    if category=='经历' and ONE_OFF.search(text):return '一次性事件，不是长期特征'
    return ''


def clean_summary(text):
    parts=[]
    for part in re.split(r'[，。；\n]',str(text or '')):
        part=part.strip()
        if not part or BIRTH_CLAUSE.match(part):continue
        if reason(part,summary=True):
            # Preserve an unrelated, factual suffix in a mixed legacy clause.
            suffix=part.rsplit('的',1)[-1] if '的' in part else ''
            if suffix and suffix!=part and not reason(suffix,summary=True) and re.search(r'音游|动漫|二次元|玩家|学生',suffix):
                parts.append(suffix)
            continue
        parts.append(part)
    return '，'.join(parts)


CATEGORY_WEIGHT={'身份':.35,'喜好':.25,'习惯':.2,'关系':.15,'雷点':.15,'经历':0.0}


def _compact(text):
    return re.sub(r'[^\w\u4e00-\u9fff]','',str(text or ''))


def covered_by_summary(fact,summary):
    """画像已经说过的信息不再重复注入，避免同一句话占两遍配额。"""
    f=_compact(fact);s=_compact(summary)
    if not f or not s:return False
    if f in s:return True
    grams={f[i:i+2] for i in range(len(f)-1)}
    return bool(grams) and sum(g in s for g in grams)/len(grams)>=.7


def select_facts(rows, query='', limit=6, now=None, summary=''):
    """Automatic use is selective; raw archives remain separately searchable."""
    now=time.time() if now is None else now
    personal=bool(re.search(r'我是谁|记得我|我的(?:画像|爱好|生日)|介绍.{0,3}我|了解我',query or ''))
    grams={query[i:i+2] for i in range(max(0,len(query)-1)) if re.fullmatch(r'[\w\u4e00-\u9fff]{2}',query[i:i+2])}
    ranked=[];seen=set()
    for row in rows:
        fact=str(row['fact'] or '')
        if reason(fact,category=row['category']) or fact in seen:continue
        # 生日已单独存储、单独注入，事实里不再重复
        if row['category']=='身份' and re.search(r'生日|出生',fact):continue
        if covered_by_summary(fact,summary):continue
        seen.add(fact)
        confidence=float(row['confidence'] or 0)
        if not math.isfinite(confidence) or confidence<.75:continue
        keys=row.keys()
        if row['category']=='经历' and 'created_at' in keys and now-float(row['created_at'] or 0)>90*86400:continue
        overlap=sum(g in fact for g in grams)
        if query and not personal and overlap==0:continue
        # 稳定特征优先：命中关键词之后，再看类别权重与置信度
        score=overlap*3+CATEGORY_WEIGHT.get(row['category'],0)+confidence
        ranked.append((score,row))
    ranked.sort(key=lambda value:-value[0])
    out=[];events=0
    for _,row in ranked:
        if row['category']=='经历':
            if events>=2:continue
            events+=1
        out.append(row)
        if len(out)>=limit:break
    return out


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
