"""Local reference classification; no model calls or source trust claims."""
import json,re,time
from aiohttp import web
from vault import seal,unseal

CATEGORIES=('考研学习','报考与院校','班级与学工','资料与文件','个人与生活','待分类')
def classify(item):
    text=str(item.get('summary',''))
    rules=[('资料与文件',r'讲义|课件|资料下载|附件|\.pdf\b|\.xlsx?\b|下载链接'),
           ('班级与学工',r'班级|班会|思政|评优|评奖|奖学金|团支部|团员|委员|辅导员'),
           ('报考与院校',r'招生|名额|报名|报考|学历校验|研招|保研|缩招|扩招|复试|调剂'),
           ('考研学习',r'408|DMA|计算机组成|操作系统|数据结构|计算机网络|数学|英语|政治|复习|解题|考研|答疑'),
           ('个人与生活',r'宿舍|校园|食堂|就医|快递|返校|个人约定')]
    category=next((c for c,p in rules if re.search(p,text,re.I)),'待分类')
    tags=[t for t,p in [('408',r'408|DMA|计算机组成|操作系统|数据结构|计算机网络'),('计算机组成原理',r'DMA|计算机组成'),('数学',r'数学|高数|线代'),('英语',r'英语'),('政治',r'政治'),('报名流程',r'报名|学历校验'),('招生政策',r'招生|名额|缩招|扩招'),('思政工作',r'思政'),('评奖评优',r'评奖|评优|奖学金'),('PDF',r'\.pdf\b')] if re.search(p,text,re.I)]
    # A quoted URL is evidence of a link, never proof that a claim was verified.
    trust='群友转述' if re.search(r'转述|传闻|据说|群友称|群友讨论|猜测',text) else '个人经验' if re.search(r'个人经验|经验分享|分享.*经验|查询经验|备考经验|亲测|心得',text) else '来源不明'
    uncertain=bool(re.search(r'未核实|待核实|未经证实|未证实|存疑|真实性未知|猜测|传闻|转述|群友称|群友讨论',text))
    return dict(category=category,tags=tags,trust=trust,uncertain=uncertain,manual=False)

def library_data(db):
    overrides={k:json.loads(unseal(p)) for k,p in db.execute('SELECT id,payload FROM reference_metadata')}
    return {k:{**classify(json.loads(unseal(p))),**overrides.get(k,{})} for k,p in db.execute("SELECT id,payload FROM matters WHERE kind='reference' AND (at>? OR starred=1) ORDER BY at DESC LIMIT 300",(time.time()-30*86400,))}

def save_metadata(db,value):
    key=value.get('id')
    if not db.execute("SELECT 1 FROM matters WHERE id=? AND kind='reference'",(key,)).fetchone():raise web.HTTPNotFound(text='资料不存在')
    if value.get('reset') is True:
        with db:db.execute('DELETE FROM reference_metadata WHERE id=?',(key,))
        return True
    category=value.get('category');tags=value.get('tags')
    if category not in CATEGORIES or not isinstance(tags,list) or len(tags)>6 or any(not isinstance(t,str) or not t.strip() or len(t)>24 for t in tags):raise web.HTTPBadRequest(text='请选择分类，标签最多6个、每个24字')
    blob=seal(json.dumps(dict(category=category,tags=list(dict.fromkeys(t.strip() for t in tags)),manual=True),ensure_ascii=False))
    with db:db.execute('INSERT OR REPLACE INTO reference_metadata VALUES(?,?)',(key,blob))
    return True
