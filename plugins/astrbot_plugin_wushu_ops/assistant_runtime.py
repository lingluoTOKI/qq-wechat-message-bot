"""Bounded, model-free support and ranking jobs for wushu_ops."""
import asyncio
import hashlib
import hmac
import json
import re
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .community_features import CommunityFeatures
from .community_closure import CommunityClosure
from .community_lifecycle import CommunityLifecycle
from .bot_utilities import BotUtilities
from .room_watch import RoomWatch

TZ = ZoneInfo('Asia/Shanghai')
FAULT = re.compile(r'打不开|进不去|连接重置|ERR_|不安全|白屏|素材缺失|掉线|断线|卡顿|报错|无响应|报问题|成绩丢|奖章不亮', re.I)
UNSOLVED = re.compile(r'还是不行|没解决|仍然|解决不了|联系管理员|联系TOKI', re.I)
SECRET = re.compile(r'(?:恢复码|token|密码|secret|authorization)\s*[:：=]\s*\S+', re.I)


def _log():
    """延迟导入宿主 logger，避免插件加载期的循环导入。"""
    from astrbot.api import logger
    return logger


def clean(text):
    return SECRET.sub('[敏感信息已省略]', str(text))[:500]


class AssistantRuntime:
    def __init__(self, plugin, path):
        self.plugin = plugin
        # 插件的消息处理与后台监控任务可能落在不同线程；放开 sqlite 的线程亲和
        # 检查，并用一把可重入锁串行化所有 DB 访问，避免并发写冲突与 ProgrammingError。
        self._db_lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
        except Exception:
            pass
        self.execute('CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT, ts REAL)')
        self.commit()
        self.lock = asyncio.Lock()
        self.community = CommunityFeatures(self)
        self.closure = CommunityClosure(self)
        self.lifecycle = CommunityLifecycle(self)
        self.utilities = BotUtilities(self)
        # 等待好友加入的同盟房间：轮询 → 3 秒倒计时 → 复核 → 播报到配置的群
        self.rooms = RoomWatch(self)

    def execute(self, sql, params=()):
        """所有 SQL 的统一入口：在锁内执行并把结果一次性取出，返回行列表。

        不向外暴露 cursor，避免调用方在锁外迭代时与并发写入打架。
        """
        with self._db_lock:
            cur = self.db.execute(sql, params)
            try:
                return cur.fetchall()
            finally:
                cur.close()

    def commit(self):
        with self._db_lock:
            return self.db.commit()

    def get(self, key, default=None):
        with self._db_lock:
            row = self.db.execute('SELECT v FROM state WHERE k=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self._db_lock:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)', (key, json.dumps(value, ensure_ascii=False), time.time()))
            self.db.commit()

    def prune(self):
        with self._db_lock:
            self.db.execute("DELETE FROM state WHERE k LIKE 'case:%' AND ts<?", (time.time()-86400,))
            self.db.execute("DELETE FROM state WHERE k LIKE 'notice:%' AND ts<?", (time.time()-7*86400,))
            self.db.execute("DELETE FROM state WHERE k LIKE 'case:%' AND k NOT IN (SELECT k FROM state WHERE k LIKE 'case:%' ORDER BY ts DESC LIMIT 100)")
            self.db.commit()

    async def send(self, umo, text):
        # Context.send_message may hide missing sessions; use the actual official
        # API response for a reliable delivery receipt. No arbitrary recipient IDs.
        from dafeiyu_runtime.delivery import send_text
        return await send_text(self.plugin.context, umo, text)

    async def message(self, event, faq):
        text = (event.message_str or '').strip()
        lifecycle_reply = await self.lifecycle.command(event)
        if lifecycle_reply is not None:
            return lifecycle_reply
        reply = await self.closure.command(event)
        if reply is not None:
            return reply
        command_reply = await self.community.command(event)
        if command_reply is not None:
            return command_reply
        if text.lstrip('/').split(' ', 1)[0] == '绑定管理员通知':
            event.stop_event()
            parts = text.lstrip('/').split()
            challenge = self.get('binding-challenge', {})
            verified = (len(parts) == 2 and time.time() < challenge.get('expires', 0)
                        and hmac.compare_digest(hashlib.sha256(parts[1].encode()).hexdigest(), challenge.get('hash', '')))
            if not event.is_private_chat() or not (event.is_admin() or verified):
                return '请在私聊中使用一次性绑定码，或由已配置的 AstrBot 管理员绑定。'
            umo = event.unified_msg_origin
            if ':FriendMessage:' not in umo:
                return '请在官方 QQ 机器人单聊中绑定。'
            self.put('admin', umo)
            with self._db_lock:
                self.db.execute("DELETE FROM state WHERE k='binding-challenge'")
                self.db.commit()
            try:
                await self.send(umo, 'TOKI 老大，管理员通知已绑定。这是主动私聊连通测试，不消耗模型 token。')
                return '绑定成功，主动私聊测试已取得官方消息回执。'
            except Exception:
                return '已保存通知会话，但主动私聊测试未取得回执；故障将保留为待处理，不会声称已送达。'
        if event.is_private_chat() or not text or text.startswith(('/', '#', '.')):
            return None
        key = 'case:' + event.unified_msg_origin + ':' + str(event.get_sender_id())
        async with self.lock:
            self.prune()
            case = self.get(key)
            if case and time.time()-case['at'] > 1800:
                case = None
            if not case and not FAULT.search(text):
                return None
            if not case:
                known_reply = await self.closure.known(event, text)
                if known_reply:
                    event.stop_event()
                    return known_reply
                incident_reply = await self.community.incident(event, clean(text))
                if incident_reply:
                    event.stop_event()
                    return incident_reply
                entry = faq(text)
                case = {'at': time.time(), 'round': 0, 'messages': [clean(text)], 'steps': clean(entry['answer']) if entry else '确认设备、报错与访问入口'}
                self.put(key, case)
                event.stop_event()
                return case['steps'] + '\n请回复设备／浏览器、访问地址和具体报错。不要清除网站数据，也不要发送身份恢复码。仍未解决可回复“还是不行”。'
            if not UNSOLVED.search(text) and not FAULT.search(text) and not re.search(r'安卓|苹果|手机|电脑|浏览器|Chrome|Safari|Edge|https?://|Wi.?Fi|流量|5G|已导入|没导入|截图|报错', text, re.I):
                return None
            case['messages'] = (case['messages'] + [clean(text)])[-3:]
            case['round'] += 1
            event.stop_event()
            if not UNSOLVED.search(text) and case['round'] < 2:
                self.put(key, case)
                return '收到。请确认系统浏览器打开、同一入口切换网络后是否仍失败；素材问题请确认本地导入完成。仍不行回复“还是不行”，我会交给 TOKI 老大。'
            notice_key = 'notice:' + hashlib.sha256(key.encode()).hexdigest()
            incident_reply = await self.community.incident(event, case['messages'][0])
            if incident_reply:
                return incident_reply
            previous = self.get(notice_key, {})
            if time.time()-previous.get('at', 0) < 1800:
                return '这个问题已登记，30 分钟内会合并，避免重复通知。'
            summary = ('【卫戍协议问题升级】\n来源：' + event.unified_msg_origin + '\n用户：' + clean(event.get_sender_name()) + '\n描述：' + '\n'.join(case['messages']) + '\n已提供步骤：' + case['steps'] + '\n时间：' + datetime.now(TZ).strftime('%m-%d %H:%M'))
            admin = self.get('admin')
            receipt = None
            if admin:
                try:
                    receipt = await self.send(admin, f'故障 #{self.closure.number(notice_key)}\n' + summary)
                except Exception as e:
                    _log().debug("[wushu_ops] 故障私聊管理员未送达，将登记为待处理: %s" % e)
            category = next((label for label,pattern in [('连接','打不开|进不去|连接重置|ERR_|不安全'),('素材','素材缺失|白屏'),('掉线','掉线|断线'),('性能','卡顿|无响应')] if re.search(pattern,case['messages'][0],re.I)), None)
            server = '契约服' if ('契约' in case['messages'][0] or '联盟' in case['messages'][0]) else ('主服' if '主服' in case['messages'][0] else '未指明服务器')
            self.put(notice_key, {'at': time.time(), 'reporters':[str(event.get_sender_id())], 'origin':event.unified_msg_origin, 'category':category,'server':server, 'status': 'sent' if receipt else 'pending', 'summary': summary, 'receipt': receipt})
            with self._db_lock:
                self.db.execute('DELETE FROM state WHERE k=?', (key,))
                self.db.commit()
            return '已通知 TOKI 老大接手处理。' if receipt else '问题已保存为待处理，当前尚未成功私聊 TOKI；可由管理员查看 /待处理问题。'

    async def bug_digest(self, data):
        now = datetime.now(TZ)
        admin = self.get('admin')
        if now.hour != 8 or not admin:
            return
        yesterday = (now - timedelta(days=1)).date()
        day = yesterday.isoformat()
        entries = []
        for bug in data.get('bugs', []):
            at = bug.get('last_report_at') or bug.get('reported_at')
            if not at:
                # Compatibility for feedback recorded before timestamp migration.
                try:
                    raw = bug.get('last_report') or bug.get('time', '')
                    stamp = datetime.strptime(str(now.year)+'-'+raw[:11], '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
                    if stamp > now:
                        stamp = stamp.replace(year=now.year-1)
                    at = stamp.timestamp()
                except (ValueError, TypeError):
                    continue
            if datetime.fromtimestamp(at, TZ).date() == yesterday:
                entries.append(f"#{bug['id']} [{clean(bug.get('status','待处理'))}] {clean(bug.get('desc',''))[:350]}\n来源：{clean(bug.get('group','未记录'))[:60]}；累计反馈 {bug.get('dup_count',1)} 次")
        if not entries:
            return
        pages = []
        for entry in entries:
            if not pages or len(pages[-1])+len(entry)>1100:
                pages.append(entry)
            else:
                pages[-1] += '\n\n'+entry
        for index, page in enumerate(pages):
            key = f'bug-digest:{day}:{index}'
            state = self.get(key, {})
            if state.get('sent') or state.get('attempts',0)>=3 or time.time()-state.get('at',0)<600:
                continue
            state.update(attempts=state.get('attempts',0)+1, at=time.time())
            self.put(key,state)
            try:
                receipt = await self.send(admin, f'【昨日 Bug 汇总】{day}（{index+1}/{len(pages)}）\n\n'+page+'\n\n以上为玩家反馈，尚不代表已确认缺陷。')
            except Exception as e:
                _log().debug("[wushu_ops] Bug 汇总第 %s 页发送失败: %s" % (index + 1, e))
                continue
            state.update(sent=True,receipt=receipt)
            self.put(key,state)

    async def polish(self, data, fallback):
        try:
            provider = self.plugin.context.get_using_provider()
            if provider is None:
                return fallback
            system_prompt = (
                "你是一条名叫大肥鱼的游戏群机器人，说话像真人：自然、短句、稍微俏皮但不夸张。"
                "根据给定的榜单数据写一条今日晨报：2到4行，最多用1个emoji；名字和分数必须与数据一致；"
                "不要编造数据里没有的内容，不要写成模板口号，不要客服腔，不要每行结构雷同。"
                "直接输出正文，不加引号，不做解释。"
            )
            resp = await provider.text_chat(prompt=data, system_prompt=system_prompt, contexts=[])
            out = (getattr(resp, "completion_text", "") or "").strip()
            return out or fallback
        except Exception as e:
            _log().debug("[wushu_ops] 榜单文案润色失败，回退模板: %s" % e)
            return fallback

    async def quip(self, data, fallback):
        try:
            provider = self.plugin.context.get_using_provider()
            if provider is None:
                return fallback
            system_prompt = (
                "你是QQ群里的机器人「大肥鱼」。根据给出的榜单数据，写一到两句口语化点评或调侃，"
                "可以夸、可以损、可以催战，但必须基于数据、不编造；不喊口号，不用客服腔，"
                "可以带1个emoji，60字以内，直接输出正文，不加引号。"
            )
            resp = await provider.text_chat(prompt=data, system_prompt=system_prompt, contexts=[])
            out = (getattr(resp, "completion_text", "") or "").strip()
            return out or fallback
        except Exception as e:
            _log().debug("[wushu_ops] 榜单点评生成失败，回退模板: %s" % e)
            return fallback

    async def rankings(self, force=False, dry=False):
        if not hasattr(self, '_ranking_lock'):
            self._ranking_lock = asyncio.Lock()
        async with self._ranking_lock:
            return await self._rankings(force=force, dry=dry)

    async def _rankings(self, force=False, dry=False):
        _log().debug("[wushu_ops] 榜单播报触发 force=%s" % force)
        targets = [t for t in self.plugin._targets() if ':GroupMessage:' in t and t.startswith('大肥鱼-官方:')]
        if not targets:
            _log().debug("[wushu_ops] 榜单播报：未配置官方群目标，跳过")
        now = datetime.now(TZ)
        if not force and (now.hour != 8 or not targets):
            return
        day = now.strftime('%Y-%m-%d')
        if not dry and targets and all(self.get('rank-sent:' + t) == day for t in targets):
            return
        attempt = self.get('rank-attempt-v2', {})
        attempts = attempt.get('count', 0) if attempt.get('day') == day else 0
        if not force and (attempts >= 3 or time.time() < attempt.get('retry_at', 0)):
            _log().debug("[wushu_ops] 榜单播报：今日已尝试过，跳过")
            return
        if not dry:
            self.put('rank-attempt-v2', {'day': day, 'count': attempts + 1,
                                       'retry_at': time.time() + 300})
        pick = random.choice
        boards = []
        for division, label in [('solo', '单人'), ('coop', '多人')]:
            # 统一走 wushu_net：自带超时/重试/JSON 校验与 ok 校验
            data = await self.plugin.net.api.daily_challenges(division, ranking=True, timeout=8)
            if data.get('rankingScope') != 'cumulative':
                raise RuntimeError('线上接口尚未提供累计榜，跳过播报避免混淆')
            rows = data.get('rows', [])
            leaders = [r for r in rows if r.get('rank') == 1 and r.get('points', 0) > 0]
            _log().debug("[wushu_ops] 榜单 %s: rows=%s, leaders=%s" % (division, len(rows), len(leaders)))
            if not leaders:
                continue
            names_list = [clean(r.get('name', '博士'))[:24] for r in leaders]
            current = [(r.get('name'), r.get('points')) for r in leaders]
            old = self.get('leader:' + division, [])
            same_set = bool(old) and sorted(x[0] for x in old) == sorted(x[0] for x in current)
            st = self.get('streak:' + division) or {}
            st = st if isinstance(st, dict) else {}
            prev_n = int(st.get('n', 0) or 0)
            last_day = st.get('day')
            if last_day == day:
                streak = max(prev_n, 1)
            elif same_set:
                streak = (prev_n or 1) + 1
            else:
                streak = 1
            if not dry: self.put('streak:' + division, {'day': day, 'n': streak})
            prev_snap = self.get('top:' + division) or {}
            prev_rows = (prev_snap.get('rows') or []) if isinstance(prev_snap, dict) and prev_snap.get('day') != day else []
            top_rows = [[r.get('rank'), clean(r.get('name', '博士'))[:24], r.get('points', 0)] for r in rows[:3]]
            if not dry: self.put('top:' + division, {'day': day, 'rows': top_rows})
            boards.append({'div': division, 'label': label, 'names': names_list, 'points': leaders[0].get('points', 0), 'streak': streak, 'tie': len(leaders) > 1, 'top': top_rows, 'prev': prev_rows})
            if not dry: self.put('leader:' + division, current)
        _log().debug("[wushu_ops] 榜单组装完成 boards=%s" % len(boards))
        if not boards:
            _log().debug("[wushu_ops] 无可播报榜单，跳过")
            if force:
                raise RuntimeError("榜单暂无可播报内容（接口未就绪或今日无成绩）")
            return
        out = []
        for b in boards:
            head = f"🏆 {b['label']}榜 第一：{'、'.join(b['names'])} {b['points']} 分"
            if b['tie']:
                head += '（并列）'
            if b['streak'] > 1:
                head += f'（连续 {b["streak"]} 天）'
            out.append(head)
            if b['top']:
                out.append('　前三：' + ' · '.join(f'{r}.{n} {p}' for r, n, p in b['top']))
                prev = {n: r for r, n, p in b['prev']}
                cur = {n: r for r, n, p in b['top']}
                chg = []
                for n, r in cur.items():
                    if n in prev and r < prev[n]:
                        chg.append(f'{n} 升到第 {r}')
                    elif n in prev and r > prev[n]:
                        chg.append(f'{n} 降到第 {r}')
                new_in = [n for n in cur if n not in prev]
                dropped = [n for n in prev if n not in cur]
                if new_in and prev:
                    chg.append('新进前三：' + '、'.join(new_in[:3]))
                if dropped and prev:
                    chg.append('掉出前三：' + '、'.join(dropped[:3]))
                out.append('📈 ' + ('；'.join(chg[:4]) if chg else ('前三名单与上一天一致' if prev else '首次记录，明日开始对比变化')))
            else:
                out.append('　（暂无前十数据）')
        opener = pick(['☀️ 早安！大肥鱼晨报来啦～', '☀️ 大肥鱼晨报 · 今日榜单', '☀️ 早安，今日榜单速递～'])
        closer = pick(['今天谁来挑战一下？', '不服的冲一波～', '想上榜的，现在开始攒分吧🐟'])
        body = opener + chr(10) + (chr(10)*2).join(out)
        # Score announcements are deterministic; previews and retry attempts
        # must not create extra paid model calls outside the budget hooks.
        text = body + chr(10)*2 + closer
        _log().debug("[wushu_ops] 晨报文案：%s" % text[:100])
        if dry:
            return text
        failures = []
        for target in targets:
            key = 'rank-sent:' + target
            if self.get(key) != day:
                try:
                    await self.send(target, text)
                    self.put(key, day)
                except Exception:
                    failures.append(target)
        if failures:
            raise RuntimeError('有%d个目标未取得发送回执，已保留重试状态' % len(failures))

    def close(self):
        with self._db_lock:
            self.db.close()
