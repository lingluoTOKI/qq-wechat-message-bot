"""Budget gate for optional direct model calls outside chat hooks."""
import asyncio
from .usage import budget_status


class ModelBudgetBlocked(RuntimeError):
    pass


async def guarded_model_call(call, **kwargs):
    allowed, _ = await asyncio.to_thread(budget_status)
    if not allowed:
        raise ModelBudgetBlocked('辅助模型调用已暂停：预算已用完或状态不可用')
    return await call(**kwargs)
