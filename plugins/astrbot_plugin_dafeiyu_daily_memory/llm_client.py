"""极简 LLM 调用：用 AstrBot 已配置的 Provider ID 直接发一次问答。

为什么要单独写：
  · 本插件绝大多数工作是 sqlite（0 token），只有"提炼人物事实"需要模型
  · 不能借用群聊主模型（deepseek-flash 有前缀缓存，多打一次就多一次冷启动风险），
    所以固定走 xiaomi/mimo-v2.6-flash 这类便宜的旁路模型

两条路径，跟 group_guardian 保持一致：
  1) context.llm_generate(chat_provider_id=..., prompt=..., system_prompt=...)
  2) 回退到 provider 实例的 text_chat()
"""
import logging

logger = logging.getLogger(__name__)


def _extract_text(resp) -> str:
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    for attr in ("completion_text", "text", "content"):
        v = getattr(resp, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    if isinstance(resp, dict):
        for k in ("completion_text", "text", "content"):
            v = resp.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return str(resp)


async def ask(context, provider_id: str, system_prompt: str, prompt: str,
              timeout: float = 120.0, max_tokens: int = 1400) -> str:
    """调用一次模型，返回纯文本。失败抛异常，由调用方决定怎么退。

    max_tokens 必须给足：默认上限常常只有 1024，而"画像 + 最多 8 条中文事实"
    很容易超，超了就是一段被切断的 JSON —— 解析失败，整批白跑。
    """
    import asyncio

    async def _run():
        if provider_id == 'zhipu/glm-5.3-flashx':
            # FlashX errors must not trigger a second paid call through fallback.
            from dafeiyu_runtime.paid import guarded_model_call
            resp = await guarded_model_call(context.llm_generate,
                chat_provider_id=provider_id,prompt=prompt,system_prompt=system_prompt,
                max_tokens=max(2400,max_tokens),request_max_retries=1)
            text = _extract_text(resp)
            if not text.strip():raise RuntimeError('智谱返回空文本，不重复调用')
            return text
        if hasattr(context, "llm_generate"):
            try:
                resp = await context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                )
                text = _extract_text(resp)
                if text.strip():
                    return text
            except TypeError:
                # 某些 provider 封装不接受 max_tokens，退回不传
                resp = await context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                )
                text = _extract_text(resp)
                if text.strip():
                    return text
            except Exception as e:
                logger.debug("[daily_memory] llm_generate 失败：%s" % str(e)[:150])
        prov = None
        if hasattr(context, "get_provider_by_id"):
            prov = context.get_provider_by_id(provider_id)
        if prov is not None and hasattr(prov, "text_chat"):
            try:
                resp = await prov.text_chat(system_prompt=system_prompt, prompt=prompt,
                                            max_tokens=max_tokens)
            except TypeError:
                resp = await prov.text_chat(system_prompt=system_prompt, prompt=prompt)
            text = _extract_text(resp)
            if text.strip():
                return text
        raise RuntimeError("Provider %s 不可用" % provider_id)

    return await asyncio.wait_for(_run(), timeout=timeout)
