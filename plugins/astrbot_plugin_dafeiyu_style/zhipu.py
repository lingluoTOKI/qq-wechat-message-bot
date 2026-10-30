"""Explicit quota-model route; no fallback to a paid model or another vendor."""
import asyncio
from types import SimpleNamespace
import httpx

MODEL='glm-5.3-flashx'
PROVIDER='zhipu/'+MODEL


async def ask(context,system,prompt,max_tokens,timeout,on_response):
    sources=context.get_config().get('provider_sources',[])
    source=next((s for s in sources if s.get('id')=='zhipu'),None)
    if not source or str(source.get('api_base','')).rstrip('/')!='https://open.bigmodel.cn/api/paas/v4':
        raise RuntimeError('智谱官方接口配置缺失或不匹配')
    key=source.get('key');key=key[0] if isinstance(key,list) and key else key
    if not isinstance(key,str) or not key:raise RuntimeError('智谱密钥未配置')
    payload={'model':MODEL,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
        'thinking':{'type':'enabled'},'reasoning_effort':'low','stream':False,'max_tokens':max(256,min(int(max_tokens),2000)),'temperature':1}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r=await client.post(source['api_base'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload)
    if r.status_code!=200:raise RuntimeError('智谱调用失败 HTTP %s，不切换其他模型'%r.status_code)
    obj=r.json();choice=obj['choices'][0]
    # Record observed usage even if output was truncated; truncated text is not learned.
    response=SimpleNamespace(id=obj.get('id'),raw_completion=obj,completion_text=choice['message'].get('content',''))
    await asyncio.to_thread(on_response,response)
    if choice.get('finish_reason')!='stop':raise RuntimeError('智谱输出不完整，保留游标')
    return response.completion_text
