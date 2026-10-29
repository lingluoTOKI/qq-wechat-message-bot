"""Official account balance, separate from usage and local budgets."""
import hashlib
import json
import sqlite3
import time
import urllib.request
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from threading import Lock
from datetime import datetime
from zoneinfo import ZoneInfo

CONFIG = '/AstrBot/data/cmd_config.json'
DB = '/AstrBot/data/plugin_data/astrbot_plugin_request_ledger/balance.db'
_lock = Lock()


def validate(payload):
    if type(payload.get('is_available')) is not bool:
        raise ValueError('invalid availability')
    rows = payload.get('balance_infos')
    if not isinstance(rows, list) or not rows:
        raise ValueError('missing balance')
    result = []
    for row in rows:
        item = {'currency': row['currency']}
        if item['currency'] not in ('CNY', 'USD'):
            raise ValueError('unknown currency')
        for key in ('total_balance', 'granted_balance', 'topped_up_balance'):
            number = Decimal(str(row[key]))
            if not number.is_finite():
                raise ValueError('invalid money')
            item[key] = str(number)
        result.append(item)
    return {'is_available': payload['is_available'], 'balance_infos': result}


def balance_text(config=CONFIG, db=DB, fetch=None, now=None):
    """Cache for 60s; failed refresh preserves a visibly stale last success.

    Only official endpoints receive keys. Persist balances, never credentials.
    Multiple configured keys cannot safely be assumed to be the same account.
    """
    now = time.time() if now is None else now
    try:
        cfg = json.loads(Path(config).read_text(encoding='utf-8-sig'))
        sources = [s for s in cfg.get('provider_sources', []) if s.get('enable', True)
                   and str(s.get('api_base', '')).rstrip('/') in
                   ('https://api.deepseek.com', 'https://api.deepseek.com/v1')]
        keys = set()
        for source in sources:
            values = source.get('key', [])
            if isinstance(values, str): values = [values]
            keys.update(k for k in values if isinstance(k, str) and k.strip())
        if len(keys) != 1:
            return '官方账户余额：暂不可用（未找到唯一官方账户凭据，未合并多账户）'
        key = next(iter(keys))
        identity = hashlib.sha256(key.encode()).hexdigest()
        with _lock:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(db, timeout=3)) as c, c:
                c.execute('CREATE TABLE IF NOT EXISTS snapshots(account TEXT,ts REAL,payload TEXT)')
                old = c.execute('SELECT ts,payload FROM snapshots WHERE account=? ORDER BY ts DESC LIMIT 1', (identity,)).fetchone()
                stale = False
                if old and 0 <= now-old[0] < 60:
                    stamp, raw = old
                    data = json.loads(raw)
                else:
                    try:
                        if fetch:
                            payload = fetch(key)
                        else:
                            req = urllib.request.Request('https://api.deepseek.com/user/balance', headers={'Authorization': 'Bearer '+key})
                            with urllib.request.urlopen(req, timeout=5) as response:
                                payload = json.load(response)
                        data = validate(payload)
                        stamp = now
                        c.execute('INSERT INTO snapshots VALUES(?,?,?)', (identity, stamp, json.dumps(data)))
                        c.execute('DELETE FROM snapshots WHERE ts<?', (now-30*86400,))
                    except Exception:
                        if not old:
                            return '官方账户余额：查询失败，金额未知（未按0元处理）'
                        stamp, raw = old
                        data = json.loads(raw)
                        stale = True
            Path(db).chmod(0o600)
        date = datetime.fromtimestamp(stamp, ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S')
        lines = []
        for row in data['balance_infos']:
            unit = '¥' if row['currency'] == 'CNY' else '$'
            lines.append(f'账户余额：{unit}{row["total_balance"]}')
        if stale:
            lines.append('刷新失败，旧快照 '+date)
        if not data['is_available']:
            lines.append('账户余额不足')
        return '\n'.join(lines)
    except Exception:
        return '官方账户余额：读取失败，金额未知'
