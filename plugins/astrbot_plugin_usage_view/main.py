"""聊天内查用量；只读成本库，费用按记录币种转换。"""
import asyncio
import json
import sqlite3
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from dafeiyu_runtime.usage import CONFIG, COSTDB, Usage, ledger, window_start, convert

STATDB = '/AstrBot/data/data_v4.db'


@register('usage_view', 'ops', '聊天里查看逐请求用量与官方校正账单', '1.3.0')
class UsageView(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    def _build(self, days):
        from pathlib import Path
        from dafeiyu_runtime.request_ledger import DB as REQUEST_DB,format_summary
        if Path(REQUEST_DB).exists():
            return format_summary(days,legacy_db=COSTDB,config=CONFIG)
        with open(CONFIG, encoding='utf-8-sig') as f:
            cfg = json.load(f)
        start = window_start(days)
        fallback = False
        try:
            u = ledger(start, db=COSTDB, target='CNY', rates=cfg.get('exchange_rates') or {})
        except (sqlite3.Error, OSError):
            fallback = True
            c = sqlite3.connect('file:' + STATDB + '?mode=ro', uri=True, timeout=3)
            try:
                n, miss, hit, out = c.execute('''SELECT COUNT(*),SUM(token_input_other),
                    SUM(token_input_cached),SUM(token_output) FROM provider_stats
                    WHERE provider_id LIKE 'deepseek%' AND created_at>=?''', (start,)).fetchone()
            finally:
                c.close()
            u = Usage(n, int(miss or 0), int(hit or 0), int(out or 0), cost_known=False)
        rate = u.hit / max(1, u.hit + u.miss) * 100
        lines = ['📊 用量 · ' + ('今天' if days == 1 else f'近{days}天'),
                 '北京时间零点起 · 成本库记录（非官方账单）',
                 f'· 框架运行记录 {u.count} 次',
                 f'· 输入 {u.miss + u.hit:,}（缓存命中 {rate:.1f}%）',
                 f'· 输出 {u.output:,}',
                 f'· 费用约 ¥{u.cost:.4f}' if u.cost_known else '· 费用未知（记录缺失、币种或汇率不可用）']
        if fallback:
            lines.append('成本库不可用，已回退框架DeepSeek统计；费用未估算。')
        if days == 1:
            tok = int((cfg.get('budgets') or {}).get('global_daily') or 0)
            cap = float((cfg.get('budgets_cost') or {}).get('global_daily') or 0)
            cur = (cfg.get('budgets_cost_currency') or {}).get('global_daily') or 'CNY'
            lines.append('今日全局预算（不代表供应商余额）')
            if tok:
                lines.append(f'· token {u.tokens:,} / {tok:,}（{u.tokens/tok:.0%}）' + (' 已达阈值' if u.tokens >= tok else ''))
            if cap:
                try:
                    cny_cap = convert(cap, cur, 'CNY', cfg.get('exchange_rates') or {})
                    label = f'· 费用上限 ¥{cny_cap:g}'
                    if u.cost_known:
                        label += f'（{u.cost/cny_cap:.0%}）' + (' 已达阈值' if u.cost >= cny_cap else '')
                    lines.append(label)
                except (ValueError, TypeError):
                    lines.append('· 费用预算币种无法转换，暂不判断费用是否超限')
        lines.append('注入已含在输入中，不重复加算；未记入成本库的MiMo／智谱等调用不在此统计内。')
        return '\n'.join(lines)

    @filter.command('用量')
    async def usage(self, event: AstrMessageEvent):
        event.stop_event()
        days = 1
        for token in (event.message_str or '').split():
            if token.isdigit():
                days = max(1, min(int(token), 30))
        try:
            from dafeiyu_runtime.balance import balance_text
            text, balance = await asyncio.gather(asyncio.to_thread(self._build, days), asyncio.to_thread(balance_text))
            text = balance + '\n\n' + text
            from dafeiyu_runtime.usage import budget_status
            _,reason=await asyncio.to_thread(budget_status,origin=event.unified_msg_origin)
            text+='\n预算：'+reason
        except Exception as exc:
            logger.warning(f'用量读取失败: {type(exc).__name__}')
            text = '用量数据暂不可用，请稍后再试；不会把未知费用显示为0元。'
        yield event.plain_result(text)
