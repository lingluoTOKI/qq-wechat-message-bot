"""Shared read-only cost ledger with explicit currency and Beijing windows."""
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
BASE = '/AstrBot/data'
COSTDB = BASE + '/plugin_data/astrbot_plugin_cost_control/supplement.db'
CONFIG = BASE + '/plugin_data/astrbot_plugin_cost_control/config.json'


def window_start(days=1, now=None, monthly=False):
    local = (now or datetime.now(TZ)).astimezone(TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start.replace(day=1) if monthly else start - timedelta(days=days-1)
    return start.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def currency(value):
    return {'¥': 'CNY', '￥': 'CNY', '$': 'USD', '€': 'EUR'}.get(str(value or '').strip(), str(value or '').strip().upper())


def convert(amount, source, target, rates):
    source, target = currency(source), currency(target)
    if not source or not target:
        raise ValueError('未知币种')
    if source == target:
        return amount
    # Same convention as cost_control: rates are units per USD.
    src = 1.0 if source == 'USD' else float(rates.get(source) or 0)
    dst = 1.0 if target == 'USD' else float(rates.get(target) or 0)
    if not math.isfinite(src) or not math.isfinite(dst) or src <= 0 or dst <= 0:
        raise ValueError('缺少有效汇率')
    return amount / src * dst


@dataclass
class Usage:
    count: int = 0
    miss: int = 0
    hit: int = 0
    output: int = 0
    injection: int = 0
    cost: float = 0.0
    cost_known: bool = True

    @property
    def tokens(self):
        return self.miss + self.hit + self.output


def ledger(start, *, db=COSTDB, target='CNY', rates=None):
    result = Usage()
    c = sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True, timeout=3)
    try:
        rows = c.execute('''SELECT COUNT(*),SUM(token_input_other),SUM(token_input_cached),
            SUM(token_output),SUM(injection_total),SUM(cost_amount),currency_symbol,
            COUNT(cost_amount) FROM cost_supplements WHERE created_at>=?
            GROUP BY currency_symbol''', (start,)).fetchall()
    finally:
        c.close()
    for n, miss, hit, output, injection, cost, cur, known in rows:
        result.count += n
        result.miss += int(miss or 0); result.hit += int(hit or 0)
        result.output += int(output or 0); result.injection += int(injection or 0)
        if known != n:
            result.cost_known = False
        try:
            amount = float(cost or 0)
            if not math.isfinite(amount):
                raise ValueError('非法金额')
            result.cost += convert(amount, cur, target, rates or {})
        except (ValueError, TypeError):
            result.cost_known = False
    return result


def budget_status(*, config=CONFIG, db=COSTDB, origin=None):
    """Prefer configured session policy; retain global bounds for legacy installs."""
    try:
        if config == CONFIG and db == COSTDB:
            from .session_budget import status
            scoped=status(origin)
            if scoped is not None:return scoped
        from .request_ledger import DB as REQUEST_DB,check_budget
        from pathlib import Path
        if db == COSTDB and Path(REQUEST_DB).exists():
            return check_budget(config=config,legacy_db=db)
        with open(config, encoding='utf-8-sig') as f:
            cfg = json.load(f)
        for dim, monthly in [('global_daily', False), ('global_monthly', True)]:
            tokens = int((cfg.get('budgets') or {}).get(dim) or 0)
            cap = float((cfg.get('budgets_cost') or {}).get(dim) or 0)
            target = (cfg.get('budgets_cost_currency') or {}).get(dim) or 'CNY'
            if tokens <= 0 and cap <= 0:
                continue
            usage = ledger(window_start(monthly=monthly), db=db, target=target, rates=cfg.get('exchange_rates') or {})
            if cap > 0 and not usage.cost_known:
                return False, '费用状态未知，跳过非必要主动请求'
            if tokens > 0 and usage.tokens >= tokens or cap > 0 and usage.cost >= cap:
                return False, dim + '预算已用完'
        return True, '预算内（仅覆盖成本库已记录用量）'
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return False, '预算数据不可用，跳过非必要主动请求'
