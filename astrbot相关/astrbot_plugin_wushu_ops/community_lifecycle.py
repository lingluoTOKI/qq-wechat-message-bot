"""Recruitment, player confirmations and notification-only maintenance."""
import hashlib
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .wushu_net import NetError, SERVERS

TZ = ZoneInfo('Asia/Shanghai')


class CommunityLifecycle:
    def __init__(self, runtime):
        self.r = runtime

    async def healthy(self, server):
        if server not in SERVERS:
            return False
        try:
            return await self.r.plugin.net.api.health_ok(server, timeout=6)
        except NetError:
            return False

    async def announce(self, key, record, phase, text):
        # Freeze recipients at scheduling; one successful delivery per phase.
        sent = record.setdefault('sent', {}).setdefault(phase, [])
        attempts = record.setdefault('attempts', {}).setdefault(phase, {})
        for target in record.get('targets', []):
            if target in sent or attempts.get(target, 0) >= 2:
                continue
            attempts[target] = attempts.get(target, 0) + 1
            self.r.put(key, record)
            try:
                await self.r.send(target, text)
                sent.append(target)
            except Exception:
                pass
            self.r.put(key, record)

    async def tick(self):
        async with self.r.lock:
            now = time.time()
            for key, raw in self.r.execute("SELECT k,v FROM state WHERE k LIKE 'maintenance:%'"):
                record = json.loads(raw)
                if record.get('finished'):
                    if record.get('terminal') and now < record['end'] + 86400:
                        phase, text = record['terminal']
                        await self.announce(key, record, phase, text)
                    continue
                start, end = record['start'], record['end']
                server = record['server']
                if now < start - 600:
                    continue
                if now < start:
                    await self.announce(key, record, 'reminder', f'🐟 {server}维护提醒：计划于北京时间 {datetime.fromtimestamp(start,TZ):%m-%d %H:%M} 开始，预计{record["minutes"]}分钟。原因：{record["reason"]}。这是维护预告，机器人不会自动重启服务。')
                    continue
                if now < end:
                    await self.announce(key, record, 'start', f'🐟 {server}已到计划维护时间，预计至 {datetime.fromtimestamp(end,TZ):%H:%M}。实际操作由管理员执行；暂勿新开局，请关注后续通知。')
                ok = await self.healthy(server)
                if not ok:
                    record['seen_down'] = True
                    record['successes'] = 0
                elif now >= end:
                    record['successes'] = record.get('successes', 0) + 1
                self.r.put(key, record)
                if now >= end and record.get('seen_down') and record.get('successes', 0) >= 2:
                    text = f'🐟 {server}维护窗口已结束，健康接口连续两次恢复响应。请刷新重连；这不代表所有玩法问题都已解决，仍有异常请反馈。'
                    record['terminal'] = ['recovered', text]
                    await self.announce(key, record, 'recovered', text)
                    record['finished'] = True
                    self.r.put(key, record)
                elif now > end + 86400:
                    # Do not endlessly poll abandoned schedules or invent recovery.
                    record['finished'] = True
                    self.r.put(key, record)

    async def command(self, event):
        parts = (event.message_str or '').strip().lstrip('/').split(maxsplit=1)
        name = parts[0] if parts else ''
        arg = parts[1] if len(parts) > 1 else ''
        if name not in {'退出开黑', '取消开黑', '结束开黑', '已解决', '维护设置', '维护状态', '维护结束', '取消维护'}:
            return None
        event.stop_event()
        async with self.r.lock:
            if name in {'退出开黑', '取消开黑', '结束开黑'}:
                if event.is_private_chat():
                    return '请在招募所在群操作。'
                self.r.community.prune()
                records = [(k,json.loads(v)) for k,v in self.r.execute("SELECT k,v FROM state WHERE k LIKE 'recruit:%'")]
                uid = str(event.get_sender_id())
                if name == '退出开黑':
                    found = [(k,v) for k,v in records if v.get('origin')==event.unified_msg_origin and self.r.closure.number(k)==arg]
                    if len(found)!=1:
                        return '请发 /退出开黑 本群有效招募的8位编号。'
                    key,value = found[0]
                    if uid==value.get('owner'):
                        return '发起人请使用 /取消开黑，不能退出自己的招募。'
                    if uid not in value.get('joined',[]):
                        return '你没有登记加入这条招募。'
                    value['joined'].remove(uid)
                    self.r.execute('UPDATE state SET v=? WHERE k=?',(json.dumps(value,ensure_ascii=False),key));self.r.commit()
                    return f'已退出 #{arg}，释放1个名额，还缺{value["slots"]-len(value["joined"])}人。原有效期不延长。'
                key = 'recruit:'+hashlib.sha256((event.unified_msg_origin+':'+uid).encode()).hexdigest()
                value = self.r.get(key)
                if not value:
                    return '你在本群没有有效招募。'
                self.r.execute('DELETE FROM state WHERE k=?',(key,));self.r.commit()
                return f'🐟 招募 #{self.r.closure.number(key)} 已由发起人取消／结束，已登记的{len(value.get("joined",[]))}名队友请知悉，不再等待。'
            if name == '已解决':
                if not arg:
                    case_key = 'case:' + event.unified_msg_origin + ':' + str(event.get_sender_id())
                    if self.r.get(case_key):
                        self.r.execute('DELETE FROM state WHERE k=?',(case_key,));self.r.commit()
                        return '已结束你当前的求助流程；若已有故障编号，请再发 /已解决 编号 确认该记录。'
                records = [(k,v) for k,v in self.r.closure.records() if v.get('origin')==event.unified_msg_origin and (not arg or self.r.closure.number(k)==arg)]
                uid = str(event.get_sender_id())
                allowed = [(k,v) for k,v in records if uid in v.get('reporters',[]) or self.r.closure.is_admin(event)]
                if len(allowed)!=1:
                    return '请在原反馈会话发送 /已解决 8位故障编号；仅原反馈者或管理员可确认。'
                key,value = allowed[0]
                confirmed = list(dict.fromkeys(value.get('confirmed',[])+[uid]))
                reporters = value.get('reporters',[])
                closed = self.r.closure.is_admin(event) or (bool(reporters) and set(reporters)<=set(confirmed))
                value.update(confirmed=confirmed, closed=closed, progress='已解决' if closed else '部分玩家确认恢复')
                self.r.put(key,value)
                return f'故障 #{self.r.closure.number(key)} '+('已确认解决并关闭。' if closed else '已记录你的恢复确认，其他反馈者仍可继续确认。')
            if name == '维护状态':
                rows = [(json.loads(v)) for _,v in self.r.execute("SELECT k,v FROM state WHERE k LIKE 'maintenance:%'")]
                active = [v for v in rows if not v.get('finished')]
                return '\n'.join(f'{v["server"]}：{datetime.fromtimestamp(v["start"],TZ):%m-%d %H:%M}—{datetime.fromtimestamp(v["end"],TZ):%m-%d %H:%M}，{v["reason"]}' for v in active) or '当前没有已登记的维护计划。'
            if not event.is_private_chat() or not self.r.closure.is_admin(event):
                return '维护计划仅 TOKI／管理员可在私聊设置；机器人只通知，不执行服务器操作。'
            if name == '维护设置':
                args = arg.split(maxsplit=4)
                if len(args)!=5 or args[0] not in SERVERS:
                    return '用法：/维护设置 主服 2026-10-24 08:00 30 更新版本（北京时间，时长分钟）'
                server,date,clock,minutes,reason = args
                try:
                    start = datetime.strptime(date+' '+clock,'%Y-%m-%d %H:%M').replace(tzinfo=TZ).timestamp()
                    minutes = int(minutes)
                    if not 0<start-time.time()<30*86400 or not 1<=minutes<=1440:
                        raise ValueError()
                except ValueError:
                    return '请填未来30天内的日期时间、1—1440分钟时长。'
                targets = [t for t in self.r.plugin._targets() if ':GroupMessage:' in t and t.startswith('大肥鱼-官方:')]
                if not targets:
                    return '没有配置官方QQ群播报目标，请先配置再设置维护。'
                key = 'maintenance:'+server
                old = self.r.get(key)
                if old and not old.get('finished'):
                    return '该服已有维护计划，请先 /取消维护 '+server+'。'
                self.r.put(key, {'server':server,'start':start,'end':start+minutes*60,'minutes':minutes,'reason':reason[:100],'targets':targets})
                return f'{server}维护计划已保存，开始前10分钟及开始时提醒；时间误差约2分钟。未检测到中断时不会自动宣布恢复，可 /维护结束 {server} 确认。'
            if arg not in SERVERS:
                return '请指定 主服 或 契约服。'
            key = 'maintenance:'+arg
            record = self.r.get(key)
            if not record or record.get('finished'):
                return '该服没有有效维护计划。'
            if name=='维护结束' and not await self.healthy(arg):
                return '健康检查未通过，不能发布恢复通知；维护计划仍保留。'
            phase = 'cancelled' if name=='取消维护' else 'confirmed'
            text = f'🐟 {arg}'+('维护计划已取消。' if name=='取消维护' else '管理员已确认维护结束，健康接口可响应，请重连；仍有异常请反馈。')
            record['terminal'] = [phase,text]
            await self.announce(key,record,phase,text)
            record['finished'] = True
            self.r.put(key,record)
            sent = len(record.get('sent',{}).get(phase,[]))
            return f'维护状态已保存，成功通知{sent}/{len(record["targets"])}个群。未送达不会声称成功。'
