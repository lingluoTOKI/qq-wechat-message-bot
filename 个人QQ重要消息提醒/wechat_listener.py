"""微信只读接入：固定源码、加密密钥缓存，不加载发送或界面修改模块。"""
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
import types

import aiohttp
from vault import seal, unseal

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'wechat-adapter-review' / 'wechatauto'


def load_reader():
    if hashlib.sha256((SOURCE/'db.py').read_bytes()).hexdigest() != '9d4eba69e868dea8000d8acdbaed723149cf8732c52e097f23fb3b07fa5ddc55':
        raise RuntimeError('Reader source changed; review required')
    # 不执行第三方包的 __init__，避免导入 UI、发送及内存写入路径。
    package = types.ModuleType('wechatauto')
    package.__path__ = [str(SOURCE)]
    sys.modules['wechatauto'] = package
    logger = types.ModuleType('wechatauto.logger')
    logger.wxlog = logging.getLogger('wechat_reader')
    logger.wxlog.disabled = True
    sys.modules['wechatauto.logger'] = logger
    spec = importlib.util.spec_from_file_location('wechatauto.db', SOURCE/'db.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class ProtectedReader(module.WeChatDB):
        def _stable_key_file(self, account=None):
            return None

        def _load_key_cache(self, path):
            try:
                data = json.loads(unseal(Path(path).read_text(encoding='utf-8')))
                return {k: bytes.fromhex(v) for k, v in data.items()}
            except Exception:
                return {}

        def _save_keys(self):
            if self._keys:
                path = Path(self.keys_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.tmp')
                temp.write_text(seal(json.dumps({k:v.hex() for k,v in self._keys.items()})), encoding='utf-8')
                os.replace(temp, path)

        def _try_other_accounts(self):
            # 不自动切换到其他历史账号。
            return False

        def _select_account_by_keys(self, candidates):
            return False

    return ProtectedReader


class Listener:
    def __init__(self):
        self.cfg = json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        self.secret = json.loads(unseal((ROOT/'secrets.dpapi').read_text()))
        self.db = sqlite3.connect(ROOT/'wechat-state.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT)')
        self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('since',str(time.time())))
        self.db.commit()
        self.reader = None

    def state(self, status):
        target=ROOT/'wechat-status.json'
        temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps({'state':status,'at':time.time()},ensure_ascii=False),encoding='utf-8')
        os.replace(temp,target)

    def connect(self):
        reader=load_reader()
        self.reader=reader(workdir=str(ROOT/'wechat-cache'),keys_file=str(ROOT/'wechat-cache'/'keys.dpapi'),account=self.cfg.get('wechat_account'))
        # 验证能读取会话，不将历史正文打印到日志。
        self.reader.get_sessions(limit=1)

    async def poll(self, session):
        since=float(self.db.execute("SELECT v FROM meta WHERE k='since'").fetchone()[0])
        own=self.reader.wxid
        for chat in self.reader.get_sessions(limit=1000):
            user=chat['username']
            if user.startswith(('gh_', 'weixin', 'filehelper')) or user==own:continue
            key=hashlib.sha256(user.encode()).hexdigest()
            row=self.db.execute('SELECT v FROM meta WHERE k=?',(key,)).fetchone()
            cutoff=float(row[0]) if row else since
            if float(chat.get('last_time') or 0)<cutoff:continue
            rows=[];offset=0
            while True:
                page=self.reader.get_messages(user,limit=100,offset=offset)
                rows.extend(m for m in page if float(m.get('create_time') or 0)>=cutoff)
                if not page or len(page)<100 or min(float(m.get('create_time') or 0) for m in page)<cutoff:break
                offset+=100
                if offset>=2000:
                    self.state('backlog_too_large');return
            newest=cutoff
            for msg in reversed(rows):
                timestamp=float(msg.get('create_time') or 0)
                newest=max(newest,timestamp)
                sender=msg.get('sender_username')
                # 无法判定发件人时不转发，防止自己的消息形成循环。
                if not sender or sender==own or msg.get('type')!='文本':continue
                text=str(msg.get('content') or '')[:1500]
                if not text:continue
                message_id=f"{msg.get('shard','')}:{msg.get('server_id',0)}:{msg.get('local_id',0)}"
                payload={'kind':'group' if user.endswith('@chatroom') else 'private','conversation':user,'sender':sender,'label':self.reader.get_nickname(user) or user,'text':text,'message_id':message_id,'timestamp':timestamp}
                async with session.post('http://127.0.0.1:'+str(self.cfg['listen_port'])+'/wechat',json=payload,headers={'Authorization':'Bearer '+self.secret['callback_token']}) as response:
                    if response.status==503:
                        # Preserve watermark and reader; queue congestion is not a login error.
                        self.state('waiting_queue')
                        return
                    response.raise_for_status()
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(newest)))
            self.db.commit()
        self.state('listening')

    async def run(self):
        if not self.cfg.get('wechat_enabled'):
            self.state('disabled');return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            while True:
                try:
                    if self.reader is None:
                        self.state('connecting');self.connect()
                    await self.poll(session)
                except (aiohttp.ClientConnectionError,asyncio.TimeoutError):
                    # A brief relay restart must not trigger a five-minute reader reset.
                    self.state('waiting_service')
                    await asyncio.sleep(5)
                    continue
                except Exception as error:
                    # 仅记录异常类型，不泄露密钥、消息内容或完整异常正文。
                    self.state('error:'+type(error).__name__)
                    self.reader=None
                    await asyncio.sleep(270)
                await asyncio.sleep(30)


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    asyncio.run(Listener().run())
