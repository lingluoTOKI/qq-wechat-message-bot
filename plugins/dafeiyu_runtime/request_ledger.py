"""Per-HTTP-request usage ledger and immutable official-export snapshots.

Only metadata/counts are stored. No prompts, replies, headers, or API keys.
Money is integer nanoyuan to avoid per-row rounding loss.
"""
import csv
import hashlib
import io
import json
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

DB = '/AstrBot/data/plugin_data/astrbot_plugin_request_ledger/requests.db'
TZ = ZoneInfo('Asia/Shanghai')
NANO = 1_000_000_000


class Ledger:
    def __init__(self, path=DB):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(
                id TEXT PRIMARY KEY,started REAL NOT NULL,ended REAL,model TEXT,
                endpoint TEXT,status TEXT NOT NULL,http_status INTEGER,
                request_id TEXT,response_id TEXT,key_hash TEXT);
            CREATE INDEX IF NOT EXISTS attempts_started ON attempts(started);
            CREATE TABLE IF NOT EXISTS charges(
                dedup TEXT PRIMARY KEY,attempt_id TEXT,started REAL NOT NULL,
                model TEXT,hit INTEGER,miss INTEGER,output INTEGER,
                cost_nano INTEGER,price_source TEXT);
            CREATE INDEX IF NOT EXISTS charges_started ON charges(started);
            CREATE TABLE IF NOT EXISTS imports(
                digest TEXT PRIMARY KEY,imported REAL NOT NULL,summary TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS official(
                day TEXT PRIMARY KEY,digest TEXT NOT NULL,imported REAL NOT NULL,
                requests INTEGER,hit INTEGER,miss INTEGER,output INTEGER,cost_nano INTEGER,
                latest_bucket TEXT,baseline REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS official_hours(
                digest TEXT,hour TEXT,requests INTEGER,hit INTEGER,miss INTEGER,
                output INTEGER,cost_nano INTEGER,PRIMARY KEY(digest,hour));
            CREATE TABLE IF NOT EXISTS official_rates(
                digest TEXT,hour TEXT,kind TEXT,nano TEXT,PRIMARY KEY(digest,hour,kind,nano));
            ''')
            c.execute("INSERT OR IGNORE INTO meta VALUES('tracking_started',?)", (str(time.time()),))
        Path(self.path).chmod(0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=3)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=3000')
        try:
            with c:yield c
        finally:c.close()

    def start(self, model, endpoint, key_hash='', started=None):
        identity = uuid.uuid4().hex
        with self.connect() as c:
            c.execute('INSERT INTO attempts(id,started,model,endpoint,status,key_hash) VALUES(?,?,?,?,?,?)',
                      (identity, started or time.time(), str(model)[:120], str(endpoint)[:96], 'started', key_hash))
        return identity

    def estimate(self, c, started, model, hit, miss, output):
        hour = datetime.fromtimestamp(started, TZ).replace(minute=0,second=0,microsecond=0).isoformat()
        # Exact exported unit prices are used only when all three are unique.
        rates = {}
        for kind in ['input_cache_hit_tokens','input_cache_miss_tokens','output_tokens']:
            values = {Decimal(r[0]) for r in c.execute(
                'SELECT nano FROM official_rates WHERE hour=? AND kind=?', (hour,kind))}
            if len(values) == 1: rates[kind] = values.pop()
        if len(rates) == 3 and model == 'deepseek-flash':
            cost = hit*rates['input_cache_hit_tokens'] + miss*rates['input_cache_miss_tokens'] + output*rates['output_tokens']
            return int(cost.to_integral_value()), 'export_hour_rate_estimate'
        if model != 'deepseek-flash':
            return None, 'unpriced_model'
        local = datetime.fromtimestamp(started, TZ)
        peak = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
        # October 29 export confirms these CNY prices. Other dates remain an
        # estimate (e.g. public holidays), never a final official charge.
        return (hit*20 + miss*1000 + output*4000) * (2 if peak else 1), '20261029_export_schedule_estimate'

    def finish(self, identity, status, *, usage=None, response_id='', request_id='', http_status=None):
        with self.connect() as c:
            row = c.execute('SELECT * FROM attempts WHERE id=?',(identity,)).fetchone()
            if row is None: return
            c.execute('UPDATE attempts SET ended=?,status=?,http_status=?,response_id=?,request_id=? WHERE id=?',
                      (time.time(),status,http_status,str(response_id)[:160],str(request_id)[:160],identity))
            if usage is None: return
            hit, miss, output = usage
            if any(type(v) is not int or v < 0 for v in usage): raise ValueError('Invalid usage')
            model = row['model']
            response_key = str(response_id or request_id or identity)
            dedup = hashlib.sha256((row['key_hash']+'\0'+response_key).encode()).hexdigest()
            prior=c.execute('SELECT hit,miss,output FROM charges WHERE dedup=?',(dedup,)).fetchone()
            if prior and tuple(prior)!=tuple(usage):
                c.execute("UPDATE attempts SET status='usage_conflict' WHERE id=?",(identity,))
                return
            cost, source = self.estimate(c,row['started'],model,hit,miss,output)
            c.execute('INSERT OR IGNORE INTO charges VALUES(?,?,?,?,?,?,?,?,?)',
                      (dedup,identity,row['started'],model,hit,miss,output,cost,source))

    def import_zip(self, path):
        raw = Path(path).read_bytes()
        buckets={};rate_rows=set();models=set();users=set();keys=set();computed=Decimal(0);cost_rows=0
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in z.namelist():
                if not name.lower().endswith('.csv'): continue
                for r in csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))):
                    if 'start_time_iso' not in r: raise ValueError('Unknown CSV schema')
                    dt=datetime.fromisoformat(r['start_time_iso'])
                    if dt.tzinfo is None:raise ValueError('CSV timestamps must include timezone')
                    dt=dt.astimezone(TZ)
                    hour=dt.isoformat();day=dt.date().isoformat()
                    b=buckets.setdefault(hour,dict(day=day,requests=0,hit=0,miss=0,output=0,cost_nano=0))
                    models.add(r.get('model',''));users.add(r.get('user_id',''))
                    if 'cost' in r:
                        cost_rows+=1
                        if r.get('currency')!='CNY': raise ValueError('Only CNY exports supported')
                        money=Decimal(r['cost'])
                        if not money.is_finite() or money<0:raise ValueError('Invalid official cost')
                        b['cost_nano']+=int((money*NANO).to_integral_value())
                    else:
                        # Only retain cardinality for scope checks, never key text.
                        keys.add(r.get('api_key',''))
                        field={'request_count':'requests','input_cache_hit_tokens':'hit',
                               'input_cache_miss_tokens':'miss','output_tokens':'output'}.get(r['type'])
                        if field:
                            quantity=int(r['amount'])
                            if quantity<0:raise ValueError('Invalid official amount')
                            b[field]+=quantity
                        if r.get('price'):
                            rate=Decimal(r['price'])*NANO
                            rate_rows.add((hour,r['type'],str(rate.normalize())))
                            computed+=Decimal(r['price'])*Decimal(r['amount'])
        if models!={'deepseek-flash'} or len(users)!=1 or len(keys)!=1:
            raise ValueError('Export must contain one account, one API key and deepseek-flash only')
        if not buckets or not cost_rows:
            raise ValueError('Export has no cost data')
        scope_hash=hashlib.sha256((next(iter(users))+'\0'+next(iter(keys))).encode()).hexdigest()
        digest=hashlib.sha256(json.dumps([scope_hash,buckets,sorted(rate_rows)],sort_keys=True).encode()).hexdigest()
        totals={}
        for hour,b in buckets.items():
            day=b['day'];total=totals.setdefault(day,dict(requests=0,hit=0,miss=0,output=0,cost_nano=0,latest_bucket=hour))
            total['latest_bucket']=max(total['latest_bucket'],hour)
            for key in ['requests','hit','miss','output','cost_nano']:total[key]+=b[key]
        summary={'days':totals,'computed_cost_nano':int((computed*NANO).to_integral_value()),
                 'scope':'one exported account/key; may include clients outside this bot', 'duplicate':False}
        now=time.time()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            scope=c.execute("SELECT v FROM meta WHERE k='official_scope_hash'").fetchone()
            if scope and scope[0]!=scope_hash:raise ValueError('Export account/API-key scope changed')
            # Recheck after locking: concurrent identical imports are idempotent.
            prior=c.execute('SELECT summary FROM imports WHERE digest=?',(digest,)).fetchone()
            if prior:return dict(json.loads(prior[0]),duplicate=True)
            # Semantic equality also covers exports written by older versions
            # and harmless decimal formatting changes. Keep the old checkpoint.
            unchanged=True
            for day,total in totals.items():
                old=c.execute('SELECT * FROM official WHERE day=?',(day,)).fetchone()
                if not old:
                    unchanged=False;break
                old_hours={tuple(r) for r in c.execute('SELECT hour,requests,hit,miss,output,cost_nano FROM official_hours WHERE digest=? AND hour LIKE ?', (old['digest'],day+'%'))}
                new_hours={(hour,b['requests'],b['hit'],b['miss'],b['output'],b['cost_nano']) for hour,b in buckets.items() if b['day']==day}
                old_rates={(r[0],r[1],Decimal(r[2])) for r in c.execute('SELECT hour,kind,nano FROM official_rates WHERE digest=? AND hour LIKE ?', (old['digest'],day+'%'))}
                new_rates={(hour,kind,Decimal(nano)) for hour,kind,nano in rate_rows if hour.startswith(day)}
                if old_hours!=new_hours or old_rates!=new_rates:
                    unchanged=False;break
            if unchanged:return dict(summary,duplicate=True)
            c.execute("INSERT OR IGNORE INTO meta VALUES('official_scope_hash',?)",(scope_hash,))
            for day,total in totals.items():
                existing=c.execute('SELECT * FROM official WHERE day=?',(day,)).fetchone()
                if existing and (total['requests'] < existing['requests'] or total['cost_nano'] < existing['cost_nano']):
                    raise ValueError('Older/lower snapshot: refusing to replace the current baseline')
                c.execute('INSERT OR REPLACE INTO official VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (day,digest,now,total['requests'],total['hit'],total['miss'],total['output'],
                           total['cost_nano'],total['latest_bucket'],now))
            for hour,b in buckets.items():
                c.execute('INSERT INTO official_hours VALUES(?,?,?,?,?,?,?)',
                          (digest,hour,b['requests'],b['hit'],b['miss'],b['output'],b['cost_nano']))
            for hour,kind,nano in rate_rows:c.execute('INSERT INTO official_rates VALUES(?,?,?,?)',(digest,hour,kind,nano))
            c.execute('INSERT INTO imports VALUES(?,?,?)',(digest,now,json.dumps(summary)))
        return summary

    def summary(self, start, end=None):
        if isinstance(start,str):
            dt=datetime.fromisoformat(start)
            start=dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        end=end or time.time()
        start_day=datetime.fromtimestamp(start,TZ).date().isoformat()
        end_day=datetime.fromtimestamp(end,TZ).date().isoformat()
        with self.connect() as c:
            rows=c.execute('SELECT * FROM charges WHERE started>=? AND started<?',(start,end)).fetchall()
            attempts=c.execute('SELECT status,COUNT(*) n FROM attempts WHERE started>=? AND started<? GROUP BY status',(start,end)).fetchall()
            official=[dict(r) for r in c.execute('SELECT * FROM official WHERE day>=? AND day<=? ORDER BY day',(start_day,end_day))]
            tracking=float(c.execute("SELECT v FROM meta WHERE k='tracking_started'").fetchone()[0])
        out={'requests':len(rows),'hit':sum(r['hit'] for r in rows),'miss':sum(r['miss'] for r in rows),
             'output':sum(r['output'] for r in rows),'observed_cost_nano':sum(r['cost_nano'] or 0 for r in rows),
             'unpriced':sum(r['cost_nano'] is None for r in rows),'states':{r['status']:r['n'] for r in attempts},
             'official':official,'tracking_started':tracking,'estimated_cost_nano':0,'estimated_tokens':0,
             'unknown_usage':sum(r['n'] for r in attempts if r['status'] in {'started','network_error','missing_usage','stream_incomplete','usage_conflict'})}
        # Official snapshots are cumulative. Never add all local rows to them.
        # Only requests started AFTER the import checkpoint are definitely new.
        by_day={r['day']:r for r in official}
        out['estimated_cost_nano']=sum(r['cost_nano'] for r in official)
        out['estimated_tokens']=sum(r['hit']+r['miss']+r['output'] for r in official)
        for r in rows:
            day=datetime.fromtimestamp(r['started'],TZ).date().isoformat();base=by_day.get(day)
            if base and r['started']<=base['baseline']:continue
            out['estimated_cost_nano']+=r['cost_nano'] or 0
            out['estimated_tokens']+=r['hit']+r['miss']+r['output']
        return out


def reconciled(start, *, db=DB, legacy_db=None):
    """A labeled budget reference, not an invented full official bill."""
    store=Ledger(db);result=store.summary(start)
    result['legacy_cost_nano']=0;result['legacy_tokens']=0
    if legacy_db and Path(legacy_db).exists():
        c=sqlite3.connect('file:'+str(legacy_db)+'?mode=ro',uri=True,timeout=3)
        try:
            rows=c.execute('''SELECT created_at,token_input_other,token_input_cached,
                token_output,cost_amount,currency_symbol FROM cost_supplements
                WHERE created_at>=?''',(start,)).fetchall()
        finally:c.close()
        official_days={r['day'] for r in result['official']}
        tracking_day=datetime.fromtimestamp(result['tracking_started'],TZ).date().isoformat()
        for ts,miss,hit,output,cost,cur in rows:
            dt=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).astimezone(TZ)
            day=dt.date().isoformat()
            # Do not combine framework and wire records on a tracked day.
            if day>=tracking_day or day in official_days:continue
            if cur=='CNY' and cost is not None:
                result['legacy_cost_nano']+=int((Decimal(str(cost))*NANO).to_integral_value())
            result['legacy_tokens']+=int(miss or 0)+int(hit or 0)+int(output or 0)
        result['estimated_cost_nano']+=result['legacy_cost_nano']
        result['estimated_tokens']+=result['legacy_tokens']
    return result


def check_budget(*, db=DB, config='/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json', legacy_db=None):
    from .usage import window_start,convert
    with open(config,encoding='utf-8-sig') as f:cfg=json.load(f)
    if cfg.get('default_on_exceeded','stop')!='stop':return True,'当前预算策略不是stop'
    for dim,monthly in [('global_daily',False),('global_monthly',True)]:
        tokens=int((cfg.get('budgets') or {}).get(dim) or 0)
        cost=float((cfg.get('budgets_cost') or {}).get(dim) or 0)
        cur=(cfg.get('budgets_cost_currency') or {}).get(dim) or 'CNY'
        if not tokens and not cost:continue
        data=reconciled(window_start(monthly=monthly),db=db,legacy_db=legacy_db)
        cap=convert(cost,cur,'CNY',cfg.get('exchange_rates') or {}) if cost else 0
        if tokens>0 and data['estimated_tokens']>=tokens:return False,dim+' Token预算已用完'
        if cap>0 and data['estimated_cost_nano']>=cap*NANO:return False,dim+'费用预算已用完（官方校正＋后续请求估算）'
    return True,'预算内；缺失usage及导出到导入之间的缺口尚未最终对账'


def format_summary(days=1, *, db=DB, legacy_db=None, config=None, detail=False):
    from .usage import window_start
    data=reconciled(window_start(days),db=db,legacy_db=legacy_db)
    if not detail:
        lines=[('今日' if days==1 else f'近{days}天')+'用量（DeepSeek）',
               f'Token：{data["estimated_tokens"]:,}',
               f'费用参考：¥{data["estimated_cost_nano"]/NANO:.4f}']
        if data['official']:
            lines.append('含官方快照，非最终账单')
        else:
            lines.append('本地采集，可能不完整')
        if config:
            try:
                from .usage import convert
                cfg=json.loads(Path(config).read_text(encoding='utf-8-sig'))
                for dim,label,monthly in [('global_daily','今日',False),('global_monthly','本月',True)]:
                    cap=float((cfg.get('budgets_cost') or {}).get(dim) or 0)
                    if cap>0:
                        cur=(cfg.get('budgets_cost_currency') or {}).get(dim) or 'CNY'
                        cap=convert(cap,cur,'CNY',cfg.get('exchange_rates') or {})
                        u=reconciled(window_start(monthly=monthly),db=db,legacy_db=legacy_db)
                        remaining=max(0,cap-u['estimated_cost_nano']/NANO)
                        lines.append(f'{label}预算剩余：¥{remaining:.2f}')
                allowed,_=check_budget(db=db,config=config,legacy_db=legacy_db)
                if not allowed:lines.append('预算已用完，付费对话暂停')
            except Exception:lines.append('预算暂不可用')
        if data['unknown_usage'] or data['unpriced']:
            lines.append('部分请求用量或费用待核对')
        return '\n'.join(lines)
    began=datetime.fromtimestamp(data['tracking_started'],TZ).strftime('%m-%d %H:%M:%S')
    lines=['📊 用量与账单 · '+('今天' if days==1 else f'近{days}天'),'北京时间统计；官方与本地分列，不使用覆盖率折算。']
    if data['official']:
        total=sum(r['cost_nano'] for r in data['official'])/NANO
        requests=sum(r['requests'] for r in data['official'])
        tokens=sum(r['hit']+r['miss']+r['output'] for r in data['official'])
        imported=datetime.fromtimestamp(max(r['imported'] for r in data['official']),TZ).strftime('%m-%d %H:%M')
        lines.append(f'· 官方导出：{requests}次API请求，{tokens:,} Token，¥{total:.6f}（{imported}导入，非全天最终）')
    else:lines.append('· 官方账单：当前窗口尚未导入')
    tokens=data['hit']+data['miss']+data['output']
    rate=100*data['hit']/(data['hit']+data['miss']) if data['hit']+data['miss'] else None
    rate_text=f'输入缓存命中{rate:.1f}%' if rate is not None else '尚无可计算缓存率的样本'
    lines.extend([f'· 逐请求采集起点：{began}',
                  f'· 当前窗口已获usage：{data["requests"]}次，{tokens:,} Token；{rate_text}',
                  f'· 输入缓存命中 {data["hit"]:,}；未命中 {data["miss"]:,}；输出 {data["output"]:,} Token',
                  f'· 本地请求费用估算：¥{data["observed_cost_nano"]/NANO:.6f}（不是供应商最终扣款）',
                  f'· 预算参考金额：¥{data["estimated_cost_nano"]/NANO:.6f}（官方快照＋导入后新增请求；旧日期可含框架估算）',
                  f'· 待核对：缺少usage {data["unknown_usage"]}次，未定价{data["unpriced"]}次；HTTP错误{data["states"].get("http_error",0)}次'])
    if config:
        try:
            allowed,reason=check_budget(db=db,config=config,legacy_db=legacy_db)
            lines.append('· '+('预算检查：允许继续' if allowed else '预算检查：停止后续付费模型请求')+'；'+reason)
            from .usage import convert
            cfg=json.loads(Path(config).read_text(encoding='utf-8-sig'))
            for dim,label,monthly in [('global_daily','今日',False),('global_monthly','本月',True)]:
                cap=float((cfg.get('budgets_cost') or {}).get(dim) or 0)
                if cap>0:
                    currency=(cfg.get('budgets_cost_currency') or {}).get(dim) or 'CNY'
                    cap=convert(cap,currency,'CNY',cfg.get('exchange_rates') or {})
                    usage=reconciled(window_start(monthly=monthly),db=db,legacy_db=legacy_db)
                    remaining=max(0,cap-usage['estimated_cost_nano']/NANO)
                    lines.append(f'· {label}预算剩余约 ¥{remaining:.6f} / ¥{cap:g}（预算参考口径，非账户余额）')
        except Exception:lines.append('· 预算检查暂不可用')
    lines.append('导出生成到导入、采集启动前/中断期间仍可能有缺口；MiMo／智谱未覆盖。官方快照与所有本地请求不会直接相加。')
    return '\n'.join(lines)
