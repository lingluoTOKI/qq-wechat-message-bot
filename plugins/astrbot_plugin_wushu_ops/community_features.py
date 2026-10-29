"""Small, on-demand community commands. No LLM or additional dependencies."""
import hashlib
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .wushu_net import NetError

HELP = '''🐟 大肥鱼使用指南
/提醒 明天 09:00 开黑；/提醒 每天 21:00 收菜；/提醒列表、/取消提醒 编号。北京时间，约2分钟检查一次。
/总结链接 https://地址：明确请求才启动网页摘要；图片与“提取文字／识别报错”同条发送可启动识图。使用模型额度，受每日0.10元／每月3元预算约束。
/新人报到 或 /欢迎：新人使用引导。重复刷屏与部分可疑链接仅提示，不自动处罚；官方QQ自动入群欢迎暂不可用。
/报bug 描述：记录问题；/bug列表：查看反馈。
反馈请附设备、浏览器、房间号、时间及截图，勿发密码和恢复码。
地址、素材、玩法、挑战等直接问，命中规则自动回答；尝试后仍未解决回复“还是不行”，发送成功才提示已通知 TOKI。
/服务器状态（或 /状态查询）：查看在线、对局及运行情况。
/在线玩家：在线玩家明细（可加“契约服”）；/协议信息：服务端版本与 WebSocket 延迟。
/等待房间：查看还在等人的同盟房间；新出现的空房倒计时3秒后自动播报房间号。
/今日挑战：今日三个目标及入榜要求。
/最新更新：当前主服版本与最近公告。
/素材帮助 安卓（或 苹果、电脑）：分设备导入指引。
/开黑 主服 终极 缺2人：发布30分钟招募。
/开黑列表：查看本群招募；/结束开黑：结束自己的招募。
/加入开黑 编号：加入指定招募，满员自动结束。
/退出开黑 编号：退出并释放名额；发起人 /取消开黑 通知本群已登记队友。
/已解决 故障编号：原反馈者确认问题恢复。
/维护状态：查看维护计划；管理员私聊 /维护设置 主服 日期 时间 分钟 原因，/维护结束 主服、/取消维护 主服。
/入门：新玩家入口卡；/额度状态：查询已记录费用与预算状态。
TOKI私聊 /故障列表、/故障处理 编号 已收到（排查中／已恢复）：更新后回原会话通知。私聊失败最多补发两次。
管理员 /bug处理 编号 状态：更新后通知原反馈会话；历史无来源记录时无法自动追溯。
每天北京时间早上8点左右播报单人／多人累计榜首，支持并列；榜单异常或无有效成绩时跳过。
@大肥鱼可聊天、点歌、查资料、锐评，例如“来一首晴天”。
上述规则功能不调用模型；@聊天、搜索等使用现有模型额度。'''


class CommunityFeatures:
    def __init__(self, runtime):
        self.r = runtime
        self.cache = {}

    async def fetch(self, path):
        cached = self.cache.get(path)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        # 统一走 wushu_net：超时、重试、状态码与 JSON 校验都在那一层
        try:
            data = await self.r.plugin.net.api.raw_get(path, timeout=8)
        except NetError as e:
            raise RuntimeError(e.user_message()) from e
        if not isinstance(data, dict):
            raise RuntimeError('接口返回格式异常')
        if data.get('ok') is not True and path != '/healthz':
            raise RuntimeError('接口未就绪')
        self.cache[path] = (time.time(), data)
        return data

    async def command(self, event):
        text = (event.message_str or '').strip().lstrip('/')
        words = text.split(maxsplit=1)
        name = words[0] if words else ''
        arg = words[1].strip() if len(words) > 1 else ''
        if name not in {'今日挑战', '最新更新', '素材帮助', '开黑', '开黑列表', '结束开黑', '运营帮助'}:
            return None
        event.stop_event()
        if name == '运营帮助':
            return HELP
        if name == '素材帮助':
            if arg not in {'安卓', '苹果', '电脑'}:
                return '发送 /素材帮助 安卓、/素材帮助 苹果 或 /素材帮助 电脑。'
            browser = {'安卓': 'Chrome 或系统浏览器', '苹果': 'Safari', '电脑': 'Chrome 或 Edge'}[arg]
            return (f'{arg}素材导入：\n① 群文件下载对应素材 ZIP，保留 ZIP 不必自行解压。\n'
                    f'② 用{browser}打开 {self.r.plugin.net.api.main_base}，不要用 QQ／微信内置浏览器。\n'
                    '③ 标题页选择“导入素材包”，选取 ZIP，保持页面前台、空间充足。\n'
                    '④ 等待“已完成 X / Y”全部完成；中断后用同一浏览器和网址重新导入。\n'
                    '长时间卡住先完全关闭页面再打开。导入和身份按浏览器及网址保存，不要清除网站数据；换入口前保存身份恢复码，勿把恢复码发群里。')
        if name in {'今日挑战', '最新更新'}:
            try:
                if name == '今日挑战':
                    data = await self.fetch('/api/daily-challenges?division=solo&ranking=0')
                    goals = '\n'.join(f'{i+1}. {c.get("name", "挑战")}：{c.get("goal", "")}' for i, c in enumerate(data.get('challenges', [])[:3]))
                    if not goals:
                        return '今日目标暂未就绪，请稍后查看。'
                    return (f'今日挑战 · {data.get("day", "")}（北京时间）\n{goals}\n'
                            '入榜：终极模拟、官方1倍首领血量、全程无AI队友及AI托管，本人存活至结算。单人独立模拟／多人2—6真人同盟模拟分榜。每天每目标首次完成+1分，每榜每天最多3分，计入累计榜。')
                health = await self.fetch('/healthz')
                data = await self.fetch('/api/announcements')
                news = data.get('items', [])[:2]
                return '主服当前版本：' + str(health.get('app', '?')) + '\n' + '\n\n'.join(str(n.get('time', '')) + ' ' + str(n.get('title', '公告')) + '\n' + re.sub('<[^>]+>', '', str(n.get('content', '')))[:650] for n in news)
            except Exception:
                return '当前查询失败，请稍后重试；不会用旧数据冒充最新内容。'
        if event.is_private_chat():
            return '开黑招募请在目标群内使用。'
        origin = event.unified_msg_origin
        key = 'recruit:' + hashlib.sha256((origin + ':' + str(event.get_sender_id())).encode()).hexdigest()
        self.prune()
        if name == '结束开黑':
            self.r.execute('DELETE FROM state WHERE k=?', (key,))
            self.r.commit()
            return '你的开黑招募已结束。'
        if name == '开黑':
            if not arg:
                return '用法：/开黑 主服 终极 缺2人（30分钟自动过期，/结束开黑 可提前结束）'
            slots = re.search(r'缺\s*([1-5])\s*人', arg)
            self.r.put(key, {'origin': origin, 'owner':str(event.get_sender_id()), 'slots':int(slots[1]) if slots else 0, 'joined':[], 'name': str(event.get_sender_name())[:24], 'text': arg[:120], 'expires': time.time()+1800})
            number = self.r.closure.number(key)
            return f'招募 #{number} 已登记（30分钟有效）：\n' + str(event.get_sender_name())[:24] + '：' + arg[:120] + f'\n队友发 /加入开黑 {number}；声明“缺1—5人”才能自动满员结束，其他描述请发起人 /结束开黑。'
        rows = self.r.execute("SELECT k,v FROM state WHERE k LIKE 'recruit:%' ORDER BY ts DESC LIMIT 100")
        import json
        entries = [(k,json.loads(v)) for k, v in rows if json.loads(v).get('origin') == origin and (not json.loads(v).get('slots') or len(json.loads(v).get('joined',[]))<json.loads(v)['slots'])]
        return '本群开黑招募：\n' + '\n'.join(f'#{self.r.closure.number(k)} {e["name"]}：{e["text"]}'+(f'（还缺{max(0,e["slots"]-len(e.get("joined",[])))}人）' if e.get('slots') else '') for k,e in entries[:10]) if entries else '本群暂无有效招募。'

    def prune(self):
        self.r.execute("DELETE FROM state WHERE k LIKE 'recruit:%' AND ts<?", (time.time()-1800,))
        self.r.execute("DELETE FROM state WHERE k LIKE 'recruit:%' AND k NOT IN (SELECT k FROM state WHERE k LIKE 'recruit:%' ORDER BY ts DESC LIMIT 100)")
        self.r.execute("DELETE FROM state WHERE k LIKE 'incident:%' AND ts<?", (time.time()-3600,))
        for prefix in ['incident:', 'bugsub:', 'bugupdate:', 'bugreceipt:']:
            self.r.execute('DELETE FROM state WHERE k LIKE ? AND k NOT IN (SELECT k FROM state WHERE k LIKE ? ORDER BY ts DESC LIMIT 500)', (prefix+'%', prefix+'%'))
        self.r.commit()

    def subscribe_bug(self, bug_id, event):
        key = 'bugsub:' + str(bug_id)
        subscribers = self.r.get(key, [])
        if event.unified_msg_origin not in subscribers:
            self.r.put(key, (subscribers + [event.unified_msg_origin])[-20:])
        try:
            rid = str(event.get_sender_id())
            if rid and rid not in ('0', 'None', ''):
                self.r.put('bugsub_open:' + str(bug_id) + ':' + event.unified_msg_origin, rid)
        except Exception:
            pass

    async def bug_update(self, bug_id, status):
        key = 'bugupdate:' + str(bug_id)
        if self.r.get(key) == status:
            return '相同状态不重复通知。'
        targets = self.r.get('bugsub:' + str(bug_id), [])
        if not targets:
            return '旧记录没有通知来源，状态已保存，无法自动通知。'
        successful = 0
        for target in targets:
            receipt_key = 'bugreceipt:' + hashlib.sha256((str(bug_id)+':'+status+':'+target).encode()).hexdigest()
            if self.r.get(receipt_key):
                successful += 1
                continue
            try:
                text = f'🐟 Bug #{bug_id} 进度更新：{status}。感谢反馈，请按最新版本复查；仍有问题可继续反馈。'
                if target.startswith('qq:GroupMessage:'):
                    rid = self.r.get('bugsub_open:' + str(bug_id) + ':' + target, '')
                    if rid:
                        text = f'<@{rid}> ' + text
                await self.r.send(target, text)
                self.r.put(receipt_key, True)
                successful += 1
            except Exception:
                pass
        if successful == len(targets):
            self.r.put(key, status)
        return f'已成功通知 {successful}/{len(targets)} 个原反馈会话；群内反馈回群通知，私聊反馈回原私聊。'

    async def incident(self, event, text):
        self.prune()
        category = next((label for label, pattern in [('连接', '打不开|进不去|连接重置|ERR_|不安全'), ('素材', '素材缺失|白屏'), ('掉线', '掉线|断线'), ('性能', '卡顿|无响应')] if re.search(pattern, text, re.I)), None)
        if category is None:
            return None
        server = '契约服' if ('契约' in text or '联盟' in text) else ('主服' if '主服' in text else '未指明服务器')
        key = 'incident:' + hashlib.sha256((event.unified_msg_origin+server+category).encode()).hexdigest()
        incident = self.r.get(key, {})
        if time.time()-incident.get('at', 0) > 600:
            incident = {'at': time.time(), 'users': [], 'status': 'new'}
        user = str(event.get_sender_id())
        incident['users'] = list(dict.fromkeys(incident['users'] + [user]))[:20]
        self.r.put(key, incident)
        if len(incident['users']) < 3:
            return None
        if incident['status'] == 'sent':
            record = self.r.get('notice:'+key,{})
            return '同类故障当前：'+record.get('progress','已登记')+'。本群已有多人反馈，已合并通知 TOKI，请关注后续公告。'
        if incident['status'] == 'pending':
            return '本群多人反馈同类故障，已合并登记，管理员通知尚未送达。'
        incident['status'] = 'pending'
        self.r.put(key, incident)
        summary = f'【集中故障提醒】同一群10分钟内有至少3名不同玩家反馈。\n来源：{event.unified_msg_origin}\n服务器：{server}\n类别：{category}\n最近描述：{text[:300]}\n这是群内反馈聚合，尚不能确认服务器故障。'
        try:
            admin = self.r.get('admin')
            if not admin:
                raise RuntimeError('未绑定')
            receipt = await self.r.send(admin, f'故障 #{self.r.closure.number("notice:"+key)}\n' + summary)
            incident['status'] = 'sent'
            self.r.put(key, incident)
            self.r.put('notice:'+key, {'at':time.time(), 'reporters':incident['users'], 'origin':event.unified_msg_origin, 'category':category,'server':server, 'status':'sent', 'summary':summary, 'receipt':receipt})
            return '本群已有多人反馈同类故障，已合并通知 TOKI；这还不能确认是服务器故障，请勿重复刷屏。'
        except Exception:
            self.r.put('notice:'+key, {'at':time.time(), 'reporters':incident['users'], 'origin':event.unified_msg_origin, 'category':category,'server':server, 'status':'pending', 'summary':summary})
            return '本群多人反馈同类故障，已合并登记，通知 TOKI 尚未送达。'
