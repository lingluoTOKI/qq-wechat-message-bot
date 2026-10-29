"""Session-scoped budget policy; a private chat never spends a group's allowance."""
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

POLICY='/AstrBot/data/plugin_data/astrbot_plugin_dafeiyu_group_guard/config.json'
COSTDB='/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db'
ORIGIN=ContextVar('dafeiyu_budget_origin',default='')
TZ=ZoneInfo('Asia/Shanghai')


@contextmanager
def scope(origin):
    token=ORIGIN.set(str(origin or ''))
    try:yield
    finally:ORIGIN.reset(token)


def group_spend(origin, cfg, *, db=COSTDB, now=None):
    from .usage import convert
    now=(now or datetime.now(TZ)).astimezone(TZ)
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    effective=cfg.get('effective_from')
    if effective:
        mark=datetime.fromisoformat(effective)
        if mark.tzinfo is None:mark=mark.replace(tzinfo=TZ)
        if start<mark<=now:start=mark
    con=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=3)
    try:
        rows=con.execute('''SELECT cost_amount,currency_symbol FROM cost_supplements
            WHERE umo=? AND julianday(created_at)>=julianday(?)
            AND julianday(created_at)<=julianday(?)''',
            (origin,start.isoformat(),now.isoformat())).fetchall()
    finally:con.close()
    total=0.0
    for amount,currency in rows:
        if amount is None:raise ValueError('Missing group cost')
        total+=convert(float(amount),currency,'CNY',cfg.get('exchange_rates') or {})
    return total*float(cfg.get('panel_to_bill',1.0))


def status(origin=None, *, policy=POLICY, db=COSTDB, now=None):
    if not Path(policy).exists():return None  # Legacy installs keep their global policy.
    try:
        cfg=json.loads(Path(policy).read_text(encoding='utf-8-sig'))
        if not cfg.get('enabled',True):return None
        origin=ORIGIN.get() if origin is None else str(origin or '')
        if ':FriendMessage:' in origin and cfg.get('private_exempt',True):
            return True,'私聊不限额'
        if ':GroupMessage:' not in origin:return False,'未确定来源会话，暂停辅助付费请求'
        cap=float(cfg.get('group_daily_budget_cny',.5))
        if cap<=0:return True,'群聊未设额度'
        spent=group_spend(origin,cfg,db=db,now=now)
        return (spent<cap, f'本群今日估算 ¥{spent:.4f} / ¥{cap:g}'+('，额度已用完' if spent>=cap else ''))
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        return False,'群聊预算状态不可用，暂停付费请求'
