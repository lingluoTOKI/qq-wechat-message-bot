import io

p = '/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_proactive_chat/core/chat_flow.py'
s = io.open(p, encoding='utf-8').read()
if '_budget_status' in s:
    print('already patched v2')
    raise SystemExit

helper = '''_BUDGET_CONFIG_PATH = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/config.json"
_PROVIDER_STATS_PATH = "/AstrBot/data/data_v4.db"


def _budget_status() -> tuple[bool, int, float, float, float]:
    """自查 cost_control 的全局日预算（token 维度 + 金额维度）。

    主动消息走 provider.text_chat() 直连，绕过 cost_control 的 on_llm_request 钩子，
    必须在发送前自己判断，否则超限后仍会发消息且不记账。

    返回 (是否超限, 今日已用 token, 今日已用金额, token 上限, 金额上限)。
    任何异常都按"未超限"处理，绝不因读配置失败而误杀正常消息。
    """
    try:
        with open(_BUDGET_CONFIG_PATH, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return False, 0, 0.0, 0, 0.0

    budgets = cfg.get("budgets") or {}
    budgets_cost = cfg.get("budgets_cost") or {}
    token_limit = int(budgets.get("global_daily") or 0)
    cost_limit = float(budgets_cost.get("global_daily") or 0)
    if token_limit <= 0 and cost_limit <= 0:
        return False, 0, 0.0, token_limit, cost_limit

    now = time.time()
    day_start = int(now - (now % 86400))
    try:
        import sqlite3

        conn = sqlite3.connect("file:" + _PROVIDER_STATS_PATH + "?mode=ro", uri=True)
        try:
            rows = list(
                conn.execute(
                    "SELECT start_time, provider_id, COALESCE(token_input_other,0), "
                    "COALESCE(token_input_cached,0), COALESCE(token_output,0) "
                    "FROM provider_stats WHERE start_time >= ?",
                    (day_start,),
                )
            )
        finally:
            conn.close()
    except Exception:
        return False, 0, 0.0, token_limit, cost_limit

    used_tokens = 0
    for r in rows:
        used_tokens += int(r[2]) + int(r[3]) + int(r[4])

    used_cost = _compute_cost_today(cfg, rows)
    over = (token_limit > 0 and used_tokens >= token_limit) or (
        cost_limit > 0 and used_cost >= cost_limit
    )
    return over, used_tokens, used_cost, token_limit, cost_limit


def _compute_cost_today(cfg: dict, rows: list) -> float:
    """按 cost_control 的配置（pricing + 时段倍率 + 汇率）估算今日花费，单位与预算一致。"""
    pricing = cfg.get("pricing") or {}
    schedules = cfg.get("pricing_schedules") or {}
    rates = cfg.get("exchange_rates") or {}
    budget_currency = ((cfg.get("budgets_cost_currency") or {}).get("global_daily")) or "CNY"
    total = 0.0
    for r in rows:
        st, pid, tok_in, tok_cached, tok_out = r
        price = pricing.get(pid)
        if not isinstance(price, dict) or price.get("mode") != "per_token":
            continue
        cost = (
            tok_in * (price.get("input") or 0)
            + tok_cached * (price.get("input_cached") or 0)
            + tok_out * (price.get("output") or 0)
        ) / 1e6
        if not cost:
            continue
        cur = price.get("currency") or "USD"
        if cur != budget_currency:
            rate = rates.get(cur)
            if cur == "USD" and budget_currency == "CNY":
                rate = rates.get("CNY")
            if not rate:
                continue
            cost = cost * rate
        cost = cost * _schedule_multiplier(schedules, pid, st)
        total += cost
    return round(total, 6)


def _schedule_multiplier(schedules: dict, pid: str, start_time: float) -> float:
    """套用 pricing_schedules 里该模型的时段倍率（峰时等）。"""
    sched = schedules.get(pid)
    if not isinstance(sched, dict) or not sched.get("enabled"):
        return 1.0
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(sched.get("timezone") or "Asia/Shanghai")
    except Exception:
        tz = None
    d = datetime.fromtimestamp(start_time, tz) if tz else datetime.fromtimestamp(start_time)
    if d.isoweekday() > 5:
        return 1.0
    mult = 1.0
    for period in sched.get("periods") or []:
        if not period.get("enabled", True):
            continue
        if not period.get("all_day") and d.weekday() + 1 not in (period.get("weekdays") or [1, 2, 3, 4, 5]):
            pass
        start_min = int(period.get("start_minute") or 0)
        end_min = int(period.get("end_minute") or 0)
        minute = d.hour * 60 + d.minute
        if not period.get("all_day") and not (start_min <= minute < end_min):
            continue
        adj = period.get("adjustment") or {}
        if adj.get("type") == "multiplier":
            mult *= float(adj.get("value") or 1.0)
    return mult

'''

anchor = 'class ProactiveCoreMixin:'
assert anchor in s
s = s.replace(anchor, helper + anchor, 1)

call_anchor = """            session_config = self._get_session_config(normalized_session_id)
            if not session_config:
                return
"""
assert call_anchor in s
new_call = """            over_budget, used_tokens, used_cost, token_limit, cost_limit = _budget_status()
            if over_budget:
                logger.info(
                    f"[主动消息] 全局日预算已用尽（token {used_tokens}/{token_limit}，"
                    f"金额 {used_cost:.4f}/{cost_limit}），跳过本次主动消息并重新调度喵。"
                )
                await self._schedule_next_chat_and_save(normalized_session_id)
                return

""" + call_anchor
s = s.replace(call_anchor, new_call, 1)
io.open(p, 'w', encoding='utf-8').write(s)
print('patched v2 OK')
