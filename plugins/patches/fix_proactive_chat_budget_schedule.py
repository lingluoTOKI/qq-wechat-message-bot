import io
p = '/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_proactive_chat/core/chat_flow.py'
s = io.open(p, encoding='utf-8').read()
old = """        if not period.get("all_day") and d.weekday() + 1 not in (period.get("weekdays") or [1, 2, 3, 4, 5]):
            pass
        start_min = int(period.get("start_minute") or 0)"""
new = """        weekdays = period.get("weekdays")
        if weekdays and d.isoweekday() not in weekdays:
            continue
        start_min = int(period.get("start_minute") or 0)"""
assert old in s, 'anchor not found'
s = s.replace(old, new, 1)
io.open(p, 'w', encoding='utf-8').write(s)
print('fixed')
