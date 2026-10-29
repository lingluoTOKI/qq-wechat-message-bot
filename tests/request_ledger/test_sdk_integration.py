"""Real installed SDK over MockTransport: never contacts the provider."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import httpx
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI=None

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from dafeiyu_runtime.request_capture import Capture
from dafeiyu_runtime.request_ledger import Ledger


@unittest.skipIf(AsyncOpenAI is None,'OpenAI-compatible SDK unavailable locally')
class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_multistep_and_stream_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Ledger(Path(folder)/'db');capture=Capture(store);capture.install();sequence=[0]
            def handler(request):
                sequence[0]+=1;identity='chatcmpl-'+str(sequence[0])
                body=json.loads(request.content)
                usage={'prompt_tokens':100,'completion_tokens':5,'total_tokens':105,
                       'prompt_cache_hit_tokens':90,'prompt_cache_miss_tokens':10,
                       'completion_tokens_details':{'reasoning_tokens':2}}
                if not body.get('stream'):
                    return httpx.Response(200,json={'id':identity,'object':'chat.completion','created':1,'model':'deepseek-flash',
                        'choices':[{'index':0,'message':{'role':'assistant','content':'fixture'},'finish_reason':'stop'}],'usage':usage})
                first={'id':identity,'object':'chat.completion.chunk','created':1,'model':'deepseek-flash',
                       'choices':[{'index':0,'delta':{'role':'assistant','content':'fixture'},'finish_reason':None}]}
                last={'id':identity,'object':'chat.completion.chunk','created':1,'model':'deepseek-flash','choices':[],'usage':usage}
                payload=('data: '+json.dumps(first)+'\n\n'+'data: '+json.dumps(last)+'\n\ndata: [DONE]\n\n').encode()
                return httpx.Response(200,content=payload,headers={'content-type':'text/event-stream'})
            client=AsyncOpenAI(api_key='fixture-not-a-secret',base_url='https://api.deepseek.com',max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            try:
                for _ in range(2):
                    response=await client.chat.completions.create(model='deepseek-flash',messages=[{'role':'user','content':'fixture'}])
                    self.assertEqual(response.choices[0].message.content,'fixture')
                stream=await client.chat.completions.create(model='deepseek-flash',messages=[{'role':'user','content':'fixture'}],
                                                           stream=True,stream_options={'include_usage':True})
                async for _ in stream:pass
                data=store.summary(0)
                self.assertEqual(data['requests'],3);self.assertEqual(data['hit'],270)
                self.assertEqual(data['miss'],30);self.assertEqual(data['output'],15)
            finally:
                await client.close();capture.uninstall()
