"""Count actual DeepSeek HTTP requests, independent of agent-run statistics."""
import asyncio
import importlib
from dafeiyu_runtime import request_ledger as _ledger_module
from dafeiyu_runtime import request_capture as _capture_module
importlib.reload(_ledger_module)
importlib.reload(_capture_module)
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star, register
from dafeiyu_runtime.request_capture import Capture
from dafeiyu_runtime.request_ledger import check_budget,format_summary
from dafeiyu_runtime.usage import COSTDB,CONFIG


@register('astrbot_plugin_request_ledger','TOKI','DeepSeek逐请求用量与官方导出校正','1.0.0')
class RequestLedgerPlugin(Star):
    def __init__(self,context,config=None):
        super().__init__(context)
        from dafeiyu_runtime import usage
        from dafeiyu_runtime import balance
        importlib.reload(balance)
        from dafeiyu_runtime import delivery
        importlib.reload(delivery)
        from dafeiyu_runtime import selection
        importlib.reload(selection)
        from dafeiyu_runtime import context as request_context
        importlib.reload(request_context)
        importlib.reload(usage)
        from dafeiyu_runtime import memory
        importlib.reload(memory)
        self.capture=Capture()
        self.capture.install()
        # Transport guard also covers tool-loop follow-up calls and direct
        # DeepSeek calls outside the normal agent hooks.
        self.capture.budget_check=lambda:usage.budget_status()
        logger.info('逐请求账本已启用：只记录usage和请求元数据，不保存聊天内容。')

    @filter.event_message_type(filter.EventMessageType.ALL,priority=100005)
    async def remember_budget_origin(self,event: AstrMessageEvent):
        from dafeiyu_runtime.session_budget import ORIGIN
        ORIGIN.set(getattr(event,'unified_msg_origin','') or '')
        from dafeiyu_runtime.profile_quality import QUERY
        QUERY.set(str(getattr(event,'message_str','') or ''))

    @filter.on_llm_request(priority=100002)
    async def budget_guard(self,event: AstrMessageEvent,req):
        try:
            from dafeiyu_runtime.session_budget import ORIGIN
            ORIGIN.set(event.unified_msg_origin)
            from dafeiyu_runtime.profile_quality import QUERY
            QUERY.set(str(getattr(event,'message_str','') or ''))
            from dafeiyu_runtime.usage import budget_status
            allowed,reason=await asyncio.to_thread(budget_status,origin=event.unified_msg_origin)
        except Exception:
            allowed,reason=False,'预算状态暂不可用'
        if not allowed:
            event.stop_event()
            await event.send(event.plain_result('本会话付费请求已暂停。/用量、/排行榜、/服务器状态仍可使用。\n'+reason))

    @filter.command('账单',alias={'请求账本'})
    async def bill(self,event: AstrMessageEvent):
        event.stop_event()
        result=await asyncio.to_thread(format_summary,legacy_db=COSTDB,config=CONFIG)
        from dafeiyu_runtime.balance import balance_text
        result=await asyncio.to_thread(balance_text)+'\n\n'+result
        from dafeiyu_runtime.usage import budget_status
        _,reason=await asyncio.to_thread(budget_status,origin=event.unified_msg_origin)
        result+='\n预算：'+reason
        if self.capture.errors:
            result+='\n部分用量采集异常'
        yield event.plain_result(result)

    async def terminate(self):
        self.capture.uninstall()
