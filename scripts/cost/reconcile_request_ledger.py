"""Read-only hourly comparison. Official request counts != framework runs."""
import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Asia/Shanghai');NANO=1_000_000_000
HOST=Path('/opt/bot-stack/astrbot/data')
BASE=HOST if HOST.is_dir() else Path('/AstrBot/data')
parser=argparse.ArgumentParser()
parser.add_argument('--day',required=True)
parser.add_argument('--db',default=str(BASE/'plugin_data/astrbot_plugin_request_ledger/requests.db'))
parser.add_argument('--legacy-db',default=str(BASE/'plugin_data/astrbot_plugin_cost_control/supplement.db'))
args=parser.parse_args()
day=datetime.strptime(args.day,'%Y-%m-%d').replace(tzinfo=TZ)
start=day.timestamp();end=(day+timedelta(days=1)).timestamp()
c=sqlite3.connect('file:'+args.db+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
official=c.execute('SELECT * FROM official WHERE day=?',(args.day,)).fetchone()
hours=defaultdict(lambda:dict(official_req=0,official_tokens=0,official_cost=0,wire_req=0,wire_tokens=0,wire_cost=0,framework_runs=0,framework_cost=0))
if official:
    for r in c.execute('SELECT * FROM official_hours WHERE digest=? AND hour LIKE ?',(official['digest'],args.day+'%')):
        h=r['hour'][:13];b=hours[h]
        b.update(official_req=r['requests'],official_tokens=r['hit']+r['miss']+r['output'],official_cost=r['cost_nano']/NANO)
for r in c.execute('SELECT * FROM charges WHERE started>=? AND started<?',(start,end)):
    h=datetime.fromtimestamp(r['started'],TZ).isoformat()[:13];b=hours[h]
    b['wire_req']+=1;b['wire_tokens']+=r['hit']+r['miss']+r['output'];b['wire_cost']+=(r['cost_nano'] or 0)/NANO
states={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM attempts WHERE started>=? AND started<? GROUP BY status',(start,end))}
began=c.execute("SELECT v FROM meta WHERE k='tracking_started'").fetchone()[0];c.close()
if Path(args.legacy_db).exists():
    c=sqlite3.connect('file:'+args.legacy_db+'?mode=ro',uri=True)
    for ts,cost,cur in c.execute('SELECT created_at,cost_amount,currency_symbol FROM cost_supplements WHERE created_at>=? AND created_at<?',
        (datetime.fromtimestamp(start,timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),datetime.fromtimestamp(end,timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))):
        h=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).astimezone(TZ).isoformat()[:13]
        hours[h]['framework_runs']+=1
        if cur=='CNY':hours[h]['framework_cost']+=float(cost or 0)
    c.close()
print('采集起点（北京时间）:',datetime.fromtimestamp(float(began),TZ).isoformat())
print('小时 | 官方API请求 | 官方Token | 官方CNY | 新账本已获usage | 新账本Token | 新账本估算CNY | 旧框架运行 | 旧框架CNY')
for hour,b in sorted(hours.items()):
    print(hour,'|',b['official_req'],'|',b['official_tokens'],'|',f"{b['official_cost']:.6f}",'|',b['wire_req'],'|',b['wire_tokens'],'|',f"{b['wire_cost']:.6f}",'|',b['framework_runs'],'|',f"{b['framework_cost']:.6f}")
print('请求状态:',json.dumps(states,ensure_ascii=False))
print('说明：采集前、导出最后一小时、进程中断或其他客户端用量不可直接逐笔匹配。费用估算与官方账单分列，不相加；重试HTTP错误不等于已计费请求。')
