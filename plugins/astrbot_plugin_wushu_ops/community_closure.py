"""Persistent support follow-up and inexpensive community utilities."""
import asyncio
import hashlib
import importlib
import json
import time
from datetime import datetime, timedelta, timezone


class CommunityClosure:
    def __init__(self, runtime):
        self.r = runtime
        self.cache = None

    def records(self):
        return [(k, json.loads(v)) for k, v in self.r.execute("SELECT k,v FROM state WHERE k LIKE 'notice:%' ORDER BY ts DESC LIMIT 200")]

    @staticmethod
    def number(key):
        return hashlib.sha256(key.encode()).hexdigest()[:8]

    def is_admin(self, event):
        return event.is_admin() or event.unified_msg_origin == self.r.get('admin')

    async def retry(self):
        async with self.r.lock:
            admin = self.r.get('admin')
            if not admin:
                return
            now = time.time()
            candidates = [(k,v) for k,v in self.records() if v.get('status') == 'pending' and not v.get('closed') and 0 <= now-v.get('at',0) < 86400 and v.get('retries',0) < 2 and now >= v.get('next_retry',v.get('at',now)+120)]
            for key, value in candidates[:2]:
                value['retries'] = value.get('retries',0)+1
                value['next_retry'] = now + 600
                self.r.put(key,value)
                try:
                    receipt = await self.r.send(admin, f'【通知补发 #{self.number(key)}】\n'+value['summary'])
                    value.update(status='sent',receipt=receipt)
                except Exception:
                    pass
                self.r.put(key,value)

    async def known(self, event, original):
        import re
        def kind(text):
            return next((name for name,pat in [('连接','打不开|进不去|连接重置|ERR_|不安全'),('素材','素材缺失|白屏'),('掉线','掉线|断线'),('性能','卡顿|无响应')] if re.search(pat,text,re.I)), None)
        category=kind(original)
        if not category:
            return None
        server='契约服' if ('契约' in original or '联盟' in original) else ('主服' if '主服' in original else '未指明服务器')
        for key,v in self.records():
            if v.get('origin') != event.unified_msg_origin or time.time()-v.get('at',0)>1800:
                continue
            if v.get('category') != category or v.get('server') != server:
                continue
            state=v.get('progress','已登记')
            if state=='已恢复':
                return f'同类问题 #{self.number(key)} 已标记恢复，请重试；若仍出现，请 /报bug 附上新情况，避免误合并。'
            return f'同类问题 #{self.number(key)} 已登记，当前：{state}。管理员通知'+('已送达。' if v.get('status')=='sent' else '待送达，最多补发两次。')
        return None

    async def budget(self, event):
        if self.cache and time.time()-self.cache[0]<30:
            return self.cache[1]
        metadata=self.r.plugin.context.get_registered_star('astrbot_plugin_cost_control')
        plugin=metadata.star_cls if metadata and metadata.activated else None
        if not plugin:
            return '成本插件未加载，无法确认额度状态。'
        base=type(plugin).__module__.rsplit('.',1)[0]+'.cost_control'
        budget=importlib.import_module(base+'.budget')
        cost=importlib.import_module(base+'.cost')
        exchange=importlib.import_module(base+'.exchange_rates')
        config=plugin.cfg
        tz=budget.resolve_tz(plugin.context)
        now=datetime.now(timezone.utc)
        daily=budget.day_window_start(config.get('refresh_time','00:00'),now,tz)
        monthly=budget.month_window_start(now,tz)
        pricing=plugin.get_pricing();currency=exchange.get_main_currency(config);rates=exchange.get_rates(config)
        values=[]
        for label,start,dimension in [('今日',daily,'global_daily'),('本月',monthly,'global_monthly')]:
            rows=await plugin.query_usage_cost_rows(pricing,start=start)
            used=cost.compute_cost_grouped_in_main(rows,pricing,currency,rates)
            limit=plugin.get_budgets_cost().get(dimension,0)
            # Current deployment uses CNY limits and main currency; explicitly
            # decline to display mismatched currency if settings later change.
            limit_currency=config.get('budgets_cost_currency',{}).get(dimension,currency)
            if limit_currency != currency:
                limit=exchange.convert(limit,limit_currency,currency,rates)
            values.append(f'{label}：{used:.4f} / {limit:.2f} {currency}' if limit else f'{label}：{used:.4f} {currency}（未设费用上限）')
        result=await plugin.check_budget(event.unified_msg_origin,None,event=event)
        stopped=result.get('exceeded') and result.get('on_exceeded')=='stop'
        state='已达预算，后续模型调用暂停' if stopped else ('已超限，仅提醒' if result.get('exceeded') else '当前未超预算')
        reset=(daily+timedelta(days=1)).astimezone(tz).strftime('%m-%d %H:%M')
        text='模型额度状态（按成本插件已记录用量）\n'+'\n'.join(values)+'\n'+state+f'\n每日窗口下次重置：{reset}；月额度下月1日重置。日/月均未超限才恢复。\n规则答疑和游戏服务不受影响；在途请求可能略超预算。'
        self.cache=(time.time(),text)
        return text

    async def command(self,event):
        parts=(event.message_str or '').strip().lstrip('/').split(maxsplit=1)
        name=parts[0] if parts else '';arg=parts[1] if len(parts)>1 else ''
        if name not in {'入门','额度状态','故障列表','故障处理','加入开黑'}:
            return None
        event.stop_event()
        if name=='入门':
            return '🐟 卫戍协议入门\n主服：https://game.lingluo-toki.top/\n契约服：https://alliance.lingluo-toki.top/\n用系统浏览器打开，手机横屏；标题页“玩法说明”看流程。\n群文件下载素材ZIP后按 /素材帮助 安卓（或 苹果、电脑）导入。\n/今日挑战 查看目标；/开黑列表 找队友。\n遇到问题说清报错，仍未解决回复“还是不行”；/报bug 描述 可记录问题。\n换设备/浏览器/网址前保存身份恢复码，勿清除网站数据或把恢复码发群里。'
        if name=='额度状态':
            try:
                async with self.r.lock:
                    return await asyncio.wait_for(self.budget(event),15)
            except Exception:
                return '额度读取失败，暂不能确认费用或暂停状态；请管理员查看成本面板。'
        if name=='加入开黑':
            if event.is_private_chat():
                return '请在招募所在群使用 /加入开黑 编号。'
            async with self.r.lock:
                self.r.community.prune()
                found=[]
                for key,value in self.r.execute("SELECT k,v FROM state WHERE k LIKE 'recruit:%'"):
                    item=json.loads(value)
                    if item.get('origin')==event.unified_msg_origin and self.number(key)==arg:
                        found.append((key,item))
                if len(found)!=1:
                    return '未找到本群有效招募，请 /开黑列表 查看8位编号。'
                key,item=found[0];uid=str(event.get_sender_id())
                if not item.get('slots'):
                    return '此招募未声明缺几人，请在群内联系发起人。'
                joined=item.get('joined',[])
                if uid==item.get('owner'):
                    return '你是招募发起人，无需加入。'
                if uid in joined:
                    return '你已经加入这条招募。'
                joined.append(uid)
                left=item['slots']-len(joined)
                if left<0:
                    return '这条招募已经满员。'
                item['joined']=joined
                # Keep original expiry when a participant joins.
                self.r.execute('UPDATE state SET v=? WHERE k=?',(json.dumps(item,ensure_ascii=False),key));self.r.commit()
                if left==0:
                    return '招募 #'+arg+' 已满员，从招募列表隐藏；请联系发起人进房。有人 /退出开黑 后可释放名额，原30分钟有效期不变。'
                return f'已加入 #{arg}，还缺 {left} 人；请在群内联系发起人。'
        if not event.is_private_chat() or not self.is_admin(event):
            return '仅 TOKI 或管理员可在私聊处理故障。'
        if name=='故障列表':
            records=self.records()[:10]
            return '\n'.join(f'#{self.number(k)} [{v.get("progress","已登记")}] {v.get("summary","")[:180]}' for k,v in records) or '暂无故障记录。'
        args=arg.split(maxsplit=1)
        if len(args)!=2 or args[1] not in {'已收到','排查中','已恢复'}:
            return '用法：/故障处理 8位编号 已收到（或 排查中、已恢复）'
        async with self.r.lock:
            found=[(k,v) for k,v in self.records() if self.number(k)==args[0]]
            if len(found)!=1:
                return '编号不存在或不唯一，请 /故障列表 查看。'
            key,value=found[0];status=args[1]
            value['progress']=status;value['closed']=status=='已恢复';self.r.put(key,value)
            origin=value.get('origin')
            if not origin:
                return '状态已保存；历史记录缺少原会话，无法自动回群通知。'
            if value.get('progress_sent')==status:
                return '已保存，相同状态不重复通知。'
            try:
                await self.r.send(origin,f'🐟 故障 #{args[0]} 进度：{status}。'+(f'请重试；原反馈者恢复后发 /已解决 {args[0]} 确认，仍有问题可补充反馈。' if status=='已恢复' else '后续处理进度将继续更新。'))
                value['progress_sent']=status;self.r.put(key,value)
                return '状态已保存，已通知原反馈会话。'
            except Exception:
                return '状态已保存，但回群通知未取得回执；可再次执行同一命令重试。'
