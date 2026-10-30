"""Attribute observed FlashX requests to their original session, without prompts."""
import sqlite3
from datetime import datetime, timezone

COSTDB='/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db'


def record(identity,origin,model,started,usage,response_id='',path=COSTDB):
    if model!='glm-5.3-flashx':return
    if not origin:raise ValueError('Missing FlashX request origin')
    hit,miss,out=usage if usage is not None else (0,0,0)
    cost=((hit+miss)*2+out*7)/1e6 if usage is not None else None
    c=sqlite3.connect(path,timeout=.2)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            key=response_id or 'wire:'+identity
            if c.execute('SELECT 1 FROM cost_supplements WHERE response_id=?',(key,)).fetchone():return
            c.execute('''INSERT INTO cost_supplements(umo,provider_id,provider_model,response_id,
                token_input_other,token_input_cached,token_output,injection_total,cost_amount,currency_symbol,created_at)
                VALUES(?,?,?,?,?,?,?,0,?,'CNY',?)''',(origin,'zhipu/glm-5.3-flashx',model,key,miss,hit,out,cost,datetime.fromtimestamp(started,timezone.utc).isoformat()))
    finally:c.close()
