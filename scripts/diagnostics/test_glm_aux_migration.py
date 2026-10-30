"""Run inside AstrBot; synthetic-only FlashX checks, no production DB writes."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

OUT=Path('/AstrBot/data/temp/glm-aux-acceptance')


async def main():
    sys.path[:0]=['/AstrBot','/AstrBot/data/pylibs','/AstrBot/data/temp']
    from context_acceptance import corpus,CUSTOM,QUESTIONS,grade_answers
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.message import Message
    from astrbot.core.provider.sources.zhipu_source import ProviderZhipu
    cfg=json.loads(Path('/AstrBot/data/cmd_config.json').read_text(encoding='utf-8-sig'))
    source=next(p for p in cfg['provider_sources'] if p['id']=='zhipu')
    config={**source,'id':'zhipu/glm-5.3-flashx','model':'glm-5.3-flashx',
        'custom_extra_body':{'thinking':{'type':'enabled'},'reasoning_effort':'low','max_tokens':3000}}
    provider=ProviderZhipu(config,cfg['provider_settings']);OUT.mkdir(exist_ok=True)
    calls=[]
    class Recorded:
        provider_config={'modalities':['text']}
        async def text_chat(self,**kw):
            if len(calls)>=4:raise RuntimeError('synthetic call limit')
            # Bound input UTF-8 bytes plus all output at conservative rates.
            upper=(len(str(kw).encode())*2+3000*7)/1e6
            if sum(c['ceiling_cny'] for c in calls)+upper>.20:raise RuntimeError('test ceiling')
            rec={'ceiling_cny':upper};calls.append(rec)
            response=await provider.text_chat(**kw,request_max_retries=1) if 'request_max_retries' not in kw else await provider.text_chat(**kw)
            raw=response.raw_completion.model_dump();u=raw.get('usage') or {}
            rec['usage']=u;rec['id']=raw.get('id');rec['gross_estimate_cny']=(u.get('prompt_tokens',0)*2+u.get('completion_tokens',0)*7)/1e6
            rec['finish_reason']=raw['choices'][0].get('finish_reason')
            if rec['finish_reason']!='stop':raise RuntimeError('truncated test output')
            return response
    recorded=Recorded();history=corpus()
    compressed=await LLMSummaryCompressor(recorded,keep_recent_ratio=.25,instruction_text=CUSTOM)(history)
    assert compressed is not history
    (OUT/'summary.txt').write_text(str(compressed[1].content))
    response=await recorded.text_chat(contexts=[*compressed[:-1],Message(role='user',content=QUESTIONS)])
    (OUT/'answers.txt').write_text(response.completion_text)
    report={'compression':grade_answers(response.completion_text)}
    response=await recorded.text_chat(prompt='合成材料：林舟喜欢钢琴曲，周末常与朋友散步；最近提醒朋友别熬夜。请写180至260字生日祝福，像熟悉的群友，包含上述具体细节，不编造共同经历，不提画像、API、模型或好感分。',system_prompt='你叫小洛，说话自然、温暖，轻微傲娇。')
    (OUT/'birthday.txt').write_text(response.completion_text)
    report['calls']=calls
    (OUT/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    await provider.client.close();print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
