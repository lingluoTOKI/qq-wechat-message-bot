from .game_account import verify_game_account
import os
import re
import json
import time
import asyncio
import difflib
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .assistant_runtime import AssistantRuntime
from .rankings import ranking_text
from .wushu_net import (NetworkManager, NetError, NetAuthError, NetConflict,
                        NetProtocolError, SERVERS, SERVER_MAIN, SERVER_ALLIANCE,
                        FAST_RETRY)

# 容器时区为 UTC，面向玩家展示的时间统一按北京时间，避免慢 8 小时
TZ = ZoneInfo('Asia/Shanghai')

from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    DATA_DIR = StarTools.get_data_dir("wushu_ops")
except Exception:
    DATA_DIR = os.path.join(PLUGIN_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
BUGS_PATH = os.path.join(DATA_DIR, "bugs.json")
FAQ_PATH = os.path.join(PLUGIN_DIR, "faq.json")
KNOWLEDGE_PATH = os.path.join(DATA_DIR, "knowledge.md")

# FAQ 条目依据 docs/QQ-GROUP-BOT.md（2026-10-22, 版本0.3.1）整理
DEFAULT_FAQ = [
    {"id": "about", "keys": ["卫戍协议", "什么游戏", "游戏介绍", "这是啥", "盟约"],
     "answer": "《卫戍协议：盟约》是一款浏览器即开即玩的策略合作同人游戏（非官方同人，与鹰角、Yostar无关，完全免费，严禁盈利）。支持2-6人联机合作（同盟模拟）或单人独立模拟；开局轮选策略，14回合最终攻势；有每日挑战、累计排行榜、31枚永久成就+21枚蚀刻章收藏。直接浏览器打开就能玩。"},
    {"id": "servers", "keys": ["服务器", "主服", "契约服", "哪个服", "网址", "地址", "链接", "进不去", "打不开"],
     "answer": "主服（日常/每日挑战/排行/收藏）：https://game.lingluotoki.dpdns.org ；契约服（独立玩法）：https://alliance.lingluo-toki.top 。域名打不开可用备用直连 http://101.132.104.41:3000 （主服）/ http://101.132.104.41:3001 （契约服）。服务器状态可开 https://game.lingluotoki.dpdns.org/healthz 查看。"},
    {"id": "assets", "keys": ["素材", "assets", "导入", "资源包", "下载素材", "1.6g", "38000"],
     "answer": "素材导入：本群群文件下载素材zip → 游戏标题页「导入素材包」→「选取zip」。约38000个文件/1.6GB，手机需2GB以上剩余空间；本地解压写入浏览器缓存不耗流量，中断重导自动断点续传。界面显示「已完成 X / Y 个文件」才是新版；显示「已接收…浏览器缓存约…」说明是旧页面——完全关闭页面（后台划掉）重开即可。"},
    {"id": "browser", "keys": ["浏览器", "什么打开", "用什么打开", "微信打开", "qq打开", "内置浏览器"],
     "answer": "浏览器即开即玩，手机横屏体验最佳。电脑用 Chrome / Edge；手机用系统浏览器或 Chrome。别用微信/QQ内置浏览器——请点「用系统浏览器打开」。"},
    {"id": "gameplay", "keys": ["怎么玩", "玩法", "流程", "难度", "模式", "回合", "新手"],
     "answer": "一局流程：确认本局信息 → 轮选策略（40名分队长，决定初始生命与规则）→ 每回合【机变→休整期→作战→联防→结算】→ 第14回合最终攻势 →（满足条件）第15回合隐秘核心 → 结算称号。模式：同盟模拟（2-6人联机）/独立模拟（单人可暂停）。难度：标准/险境/绝境/终极（另有训练、欢乐）。更多细节看游戏内标题页「玩法说明」和「S.W.E.E.P. 报告」。"},
    {"id": "ban", "keys": ["ban", "禁用", "bans", "开局禁"],
     "answer": "开局BAN规则：基础禁用核心3+附加4个盟约；5人（含AI）少禁1个核心、6人少禁2个（即6人局核心1/附加4）。训练模式不减免。"},
    {"id": "reconnect", "keys": ["掉线", "断线", "重连", "回房间", "接回"],
     "answer": "掉线后60秒内重新打开页面自动回到原座位（同盟/独立/重启恢复统一60秒）。同盟掉线期间你的阵容照常自动作战但不会替你购买；想让AI代打请选「暂离（AI托管）」。独立模拟作战可按Space暂停。服务器重启后回到最近一次休整期存档，60秒内刷新接回。"},
    {"id": "busy", "keys": ["繁忙", "满员", "房间满", "开不了房"],
     "answer": "「服务器繁忙」= 房间数达到上限（64间）。服务器会自动清理无人房间，一般1分钟内释放，稍等重试即可。持续出现请向管理反馈。"},
    {"id": "save", "keys": ["存档", "重启", "恢复失败", "进度没了"],
     "answer": "服务器重启后对局回到最近一次休整期存档，60秒内重新打开页面接回；作战回合中的进度不落盘，会重打该回合。若提示「对局恢复失败」：存档被安全机制拒绝（checkpoint后有人离开过），只能重开，成绩与奖章不受影响。"},
    {"id": "daily", "keys": ["每日挑战", "挑战", "积分", "排行榜", "排行"],
     "answer": "每日挑战：每天3项目标，每项目标当天首次完成+1分、每天最多3分；积分进累计榜长期累加，单人/多人分榜，同分并列。目标按七天轮换，新一轮仍可得分。"},
    {"id": "medal", "keys": ["成就", "蚀刻章", "收藏", "奖章"],
     "answer": "收藏系统：永久成就31枚+每日挑战21枚蚀刻章，只显示章面，真实获得才点亮。"},
    {"id": "recovery", "keys": ["恢复码", "成绩丢了", "找不到了", "数据丢了", "换手机", "清缓存"],
     "answer": "换了浏览器或清空了网站数据导致成绩/奖章不见：用「身份恢复码」找回（每日挑战页面里有恢复入口）。一份恢复码同时找回每日与永久身份，务必保存好；没保存的话无法找回。"},
    {"id": "lag", "keys": ["卡", "卡顿", "很卡", "加载慢", "白屏", "转圈", "慢"],
     "answer": "卡顿排查：①确认用系统浏览器而非QQ内置；②手机切换WiFi/流量再试；③晚间高峰卡顿属正常；④只有你打不开而别人正常才是你这边的问题，大家都打不开就是服务器问题等公告。"},
    {"id": "wrongphase", "keys": ["wrong_phase", "没反应", "点了没反应", "无响应"],
     "answer": "操作没反应/提示WRONG_PHASE：刷新页面重新进入。频繁出现请截图带房间号反馈给管理。"},
    {"id": "notch", "keys": ["刘海", "挖孔", "异形屏", "挡住", "遮挡"],
     "answer": "手机屏幕被挖孔/刘海挡住：设置 →「异形屏适配」，四边滑块可微调，50为默认值。"},
    {"id": "feedback", "keys": ["问题反馈", "诊断", "怎么反馈", "反馈bug"],
     "answer": "反馈bug推荐两种方式：①游戏内「设置 → 问题反馈 → 复制诊断信息」，把诊断信息+发生了什么+房间号/时间发给管理；②直接发 /报bug 具体描述，自动记录。诊断信息自动隐藏玩家名，不含密码。"},
    {"id": "tips", "keys": ["技巧", "攻略", "怎么打", "建议"],
     "answer": "精选小技巧：前期优先凑出一个能激活的盟约；资金不留到下回合，想要的卡先冻结；看到飞行/近地悬浮敌人留好对空；同盟里和队友分工凑不同盟约；LP只剩10左右优先保完美作战；领袖战单次伤害超过30万会被作废（限伤）。"},
    {"id": "bug", "keys": ["报bug", "bug反馈", "bug提交", "反馈bug"],
     "answer": "发现bug直接发 /报bug 具体描述（如：/报bug 契约服进战斗后地图黑一块），会自动记录编号，官方定期整理修复。游戏内的诊断信息也可以一并贴给管理。"},
    {"id": "pwa", "keys": ["主屏幕", "桌面", "图标", "添加到"],
     "answer": "手机推荐添加到主屏幕，更稳更省内存：安卓 Chrome 右上角⋮ → 添加到主屏幕；iOS Safari 分享 → 添加到主屏幕。"},
]


# ---------- 素材文件读盘缓存 ----------
# FAQ / 知识库是静态素材，但原先每条群消息都会读盘 2~3 次。
# 这里按「文件签名（mtime_ns + size）」缓存解析结果，文件一改立即失效。
_FILE_CACHE = {}
_FILE_CACHE_LOCK = threading.RLock()


def _file_signature(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_file_cached(path, parse, fallback):
    """带失效判断的文件读取；parse 接收已打开的文件对象。"""
    sig = _file_signature(path)
    if sig is None:
        return fallback
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(path)
        if hit and hit[0] == sig:
            return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = parse(f)
    except Exception:
        return fallback
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[path] = (sig, value)
    return value


def _load_bugs():
    if os.path.exists(BUGS_PATH):
        try:
            with open(BUGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seq": 0, "bugs": []}


def _save_bugs(data):
    with open(BUGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_faq(f):
    data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("entries"), list) and data["entries"]:
        return data["entries"]
    if isinstance(data, list) and data:
        return data
    return None


def _load_faq():
    entries = _read_file_cached(FAQ_PATH, _parse_faq, None)
    if entries:
        return entries
    _save_faq(DEFAULT_FAQ)
    return DEFAULT_FAQ


def _save_faq(data):
    with open(FAQ_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def match_faq(text):
    text = (text or "").lower()
    if not text:
        return None
    entries = _load_faq()
    try:
        entries = sorted(entries, key=lambda e: -int(e.get("priority", 0)))
    except Exception:
        pass
    for entry in entries:
        keys = [k.lower().strip() for k in entry.get("keywords", entry.get("keys", [])) if k]
        keys = [k for k in keys if len(k) >= 2 and k not in _STOP_KEYWORDS]
        if not keys:
            continue
        # 强关键词（4 字以上，如「服务器地址」「导入卡住」「新人教程」）命中一个即可；
        # 弱关键词（2-3 字，如「免费」「官方」「模式」「卡了」）在闲聊里几乎必然出现，
        # 必须命中 2 个以上才算命中 —— 这正是"同一段说明被复读好几遍"的根因。
        strong = [k for k in keys if len(k) >= 4]
        weak = [k for k in keys if len(k) < 4]
        if any(k in text for k in strong):
            return entry
        if weak and sum(1 for k in weak if k in text) >= 2:
            return entry
    return None


def _find_duplicate(desc, bugs, threshold=0.5):
    d = re.sub(r"\s+", "", desc or "")
    if not d:
        return None
    best, best_score = None, 0.0
    for b in bugs:
        s = difflib.SequenceMatcher(None, d, re.sub(r"\s+", "", b.get("desc", ""))).ratio()
        if s > best_score:
            best, best_score = b, s
    return best if best_score >= threshold else None


def _load_knowledge():
    return _read_file_cached(KNOWLEDGE_PATH, lambda f: f.read(), "") or ""


# 章节切分结果随文本内容缓存，避免同一次查询里重复解析
_SECTIONS_CACHE = {"text": None, "sections": []}


def _knowledge_sections():
    text = _load_knowledge()
    with _FILE_CACHE_LOCK:
        if _SECTIONS_CACHE["text"] == text:
            return _SECTIONS_CACHE["sections"]
    sections = []
    if text:
        for p in text.split("\n## "):
            p = p.strip()
            if not p:
                continue
            lines = p.split("\n", 1)
            title = lines[0].lstrip("# ").strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            sections.append({"title": title, "body": body})
    with _FILE_CACHE_LOCK:
        _SECTIONS_CACHE["text"] = text
        _SECTIONS_CACHE["sections"] = sections
    return sections


# 知识库小于该字符数时不做章节筛选，直接全量返回。
# 原因：章节检索只取 top-2，命中时给出的内容反而比「未命中返回全文」更少，
# 相关内容常因排在第三、第四章节而被永久挡在外面。小库全量进 prompt 更全，
# 且这点 token 在 DeepSeek 前缀缓存命中时成本可忽略。
SMALL_KB_CHARS = 8000


def search_knowledge(question, max_chars=2500):
    # 全文只读一次：命中章节与兜底截断共用同一份内容
    text = _load_knowledge()
    if not text:
        return ""
    if len(text) <= SMALL_KB_CHARS:
        return text
    q = (question or "").lower()
    if not q:
        return text[:max_chars]
    scored = []
    for sec in _knowledge_sections():
        hay = (sec["title"] + "\n" + sec["body"]).lower()
        score = sum(1 for kw in set(q) if len(kw) > 1 and kw in hay)
        scored.append((score, sec))
    scored.sort(key=lambda x: -x[0])
    picked = [s for s in scored if s[0] > 0][:2]
    if not picked:
        return text[:max_chars]
    out = ""
    for _, sec in picked:
        chunk = "【%s】\n%s" % (sec["title"], sec["body"])
        if len(out) + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - len(out))]
        out += chunk + "\n\n"
        if len(out) >= max_chars:
            break
    return out.strip()


STATUS_KEYS = ["是不是炸了", "炸了吗", "开服了吗", "开服没有", "服务器状态", "服务器挂了", "服务器怎么样", "健康检查"]

# 疑问特征词：用于判断「这条消息是不是在提问」。
# 刻意不含裸的「吗 / 呢 / 哪 / 几」——这些是日常超高频虚词，
# 早先把它们当提问特征，导致群友闲聊一句「行吗」就被 bot 抢答。
_QUESTION_MARKERS = (
    "?", "？", "怎么", "怎样", "如何", "什么", "为啥", "为什么", "多少",
    "哪里", "哪儿", "是否", "能否", "能不能", "是不是", "可以吗", "行不行",
    "求助", "请问", "谁知道", "能告诉我", "有吗",
)

# 关键词黑名单：即使写进 FAQ 的 keywords 也不参与匹配
_STOP_KEYWORDS = {
    "吗", "呢", "哪", "几", "啊", "吧", "哦", "嗯", "呀", "嘛", "哈", "呵",
    "什么", "怎么", "为什么", "这个", "那个", "就是", "可以", "没有", "一个",
}

# 两个服务器名与地址统一由 wushu_net.NetworkManager 管理（可在插件配置里改）
STATUS_SERVERS = SERVERS
LOCAL_PORTS = [(3000, "主服"), (3001, "契约服")]


def _uptime_str(up):
    up = int(up or 0)
    if up >= 3600:
        return "%.1f小时" % (up / 3600)
    return "%d分钟" % (up // 60)


def _round_load(load):
    if isinstance(load, (list, tuple)):
        return " / ".join(str(round(float(x), 2)) for x in load)
    return str(load)


async def _build_status_text(net):
    out = ["\U0001F3AE 卫戍协议 · 服务器状态", ""]
    try:
        pub = {}
        for name in STATUS_SERVERS:
            try:
                pub[name] = (True, await net.api.health(name, timeout=6, retry=FAST_RETRY))
            except NetError as e:
                pub[name] = (False, e.user_message())
        loc = {}
        for port, name in LOCAL_PORTS:
            try:
                loc[name] = await net.local.stability(port)
            except NetError as e:
                loc[name] = {"ok": False, "error": e.user_message()}
        for name in STATUS_SERVERS:
            ok, data = pub[name]
            d = loc.get(name, {})
            if not ok:
                out.append("%s ❌ 公网无响应（%s）" % (name, str(data)[:60]))
                out.append("")
                continue
            app = str(data.get("app", "?"))
            humans = data.get("humans", 0)
            rooms = data.get("rooms", 0)
            matches = data.get("matches", 0)
            rec = data.get("recovery")
            pend = rec.get("pending", 0) if isinstance(rec, dict) else 0
            out.append("%s ✅ 在线" % name)
            out.append("　版本 v%s · 已运行 %s" % (app, _uptime_str(data.get("uptimeSec", 0))))
            line = "　在线 %s人 · 房间 %s · 对局 %s" % (humans, rooms, matches)
            if pend:
                line += " · 待恢复 %s" % pend
            out.append(line)
            if d.get("ok"):
                active = "准入已开" if d.get("active") else "准入已暂停"
                extra = "" if not d.get("observationStale") else " · 数据可能过期"
                age = d.get("observationAgeSec")
                if age is not None:
                    extra += " · 观测 %d 秒前" % int(age)
                out.append("　本地：%s%s" % (active, extra))
                # 服务端进程与房间细节：原来只报主机层，出问题时无从判断是服务还是网络
                svc = d.get("service") or {}
                svc_health = svc.get("health") or {}
                bits = []
                rss = svc.get("MemoryCurrent")
                if rss:
                    bits.append("内存 %dMB" % (int(rss) // 1048576))
                restarts = svc.get("NRestarts")
                if restarts is not None:
                    bits.append("重启 %s 次" % restarts)
                players = svc.get("connectedPlayers")
                if players is not None:
                    bits.append("在线 %s 人" % players)
                away = svc.get("awayRooms")
                if away:
                    bits.append("挂机房间 %s" % away)
                adm = d.get("admission") or {}
                lag = adm.get("lagMs")
                if lag is not None:
                    bits.append("延迟 %dms" % int(lag))
                if bits:
                    out.append("　服务端：" + " · ".join(bits))
                if svc.get("observerError"):
                    out.append("　⚠️ 观测异常：%s" % svc.get("observerError"))
                if svc_health.get("ok") is False:
                    out.append("　⚠️ 服务端自检未通过")
            out.append("")
        host_d = next((loc[n] for n in loc if loc[n].get("ok")), None)
        if host_d:
            host = host_d.get("host", {})
            out.append("\U0001F5A5 主机（两服同机）")
            out.append("　内存剩余 %sMB · 交换已用 %sMB · 负载 %s" % (
                host.get("availableMB"), host.get("swapUsedMB"), _round_load(host.get("load", []))))
            warns = host.get("warnings") or []
            if warns:
                out.append("　⚠️ %s" % "；".join(warns))
            # 长时间挂机房间：只会占内存却没人玩，是小机器上的隐性负担
            for _name, _d in loc.items():
                _svc = _d.get("service") or {}
                _old = _svc.get("awayOver2h")
                if _old:
                    out.append("　⚠️ %s 有 %s 个房间挂机超 2 小时" % (_name, _old))
        out.append("")
        out.append("（公网 + 本地双链路实时检测）")
    except Exception as e:
        out.append("查询出错：%s" % str(e)[:60])
    return "\n".join(out)


@register("astrbot_plugin_wushu_ops", "TOKI",
          "《卫戍协议：盟约》群运营助手：bug收集管理 + 游玩知识库自动答疑 + 等待房间自动播报", "v1.8.4")
class WushuOpsPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._cooldown = {}
        # 两把锁分开：bugs.json 的读改写用 _bug_lock；状态查询内部要打最多 4 次
        # HTTP（各 6 秒超时），独占 _status_lock，避免慢查询阻塞所有人报 bug。
        self._bug_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._lock = self._bug_lock  # 兼容历史引用
        self._task = None
        self._state = {}
        self._fails = {}
        self._announced = {}
        self.assistant = AssistantRuntime(self, os.path.join(DATA_DIR, 'assistant.sqlite'))
        # 所有服务端交互（HTTP /api/*、管理接口、本地状态代理、/ws）统一走这一层
        self.net = NetworkManager(self.config)
        self._status_cache = (0, '')
        self._status_ttl = 30
        self._status_inflight = None

    async def initialize(self):
        self._ensure_monitor()
        self.assistant.rooms.start()

    @filter.command("绑定游戏")
    async def bind_game_account(self, event: AstrMessageEvent, realm: str = "", code: str = ""):
        event.stop_event()
        yield event.plain_result(await verify_game_account(event, self.config, realm, code))

    # ---------- bug 收集 ----------

    @filter.command("报bug", alias={"bug提交"})
    async def report_bug(self, event: AstrMessageEvent, text: str = ""):
        """上报bug：/报bug 具体描述"""
        event.stop_event()
        desc = (text or "").strip()
        if not desc:
            yield event.plain_result("用法：/报bug 具体描述（例如：/报bug 契约服进战斗后地图黑一块）")
            return
        async with self._bug_lock:
            data = _load_bugs()
            dup = _find_duplicate(desc, data.get("bugs", []))
            if dup:
                dup["dup_count"] = dup.get("dup_count", 1) + 1
                dup["last_report"] = datetime.now(TZ).strftime("%m-%d %H:%M") + " " + (event.get_sender_name() or str(event.get_sender_id()))
                dup['last_report_at'] = time.time()
                _save_bugs(data)
            else:
                data["seq"] += 1
                item = {
                    "id": data["seq"],
                    "time": datetime.now(TZ).strftime("%m-%d %H:%M"),
                    "sender": event.get_sender_name() or str(event.get_sender_id()),
                    "group": event.get_group_id() or "私聊",
                    "desc": desc[:500],
                    "status": "待处理",
                    "dup_count": 1,
                    "reported_at": time.time(),
                }
                data["bugs"].append(item)
                _save_bugs(data)
            self.assistant.community.subscribe_bug(dup['id'] if dup else item['id'], event)
        if dup:
            reply = f"已合并到反馈 #{dup['id']}，当前状态：{dup.get('status','待处理')}。感谢补充。"
        else:
            reply = f"已记录反馈 #{item['id']}：{desc[:80]}。当前状态：待核实。可补充房间号、发生时间、复现步骤及截图。"
        if re.search(r"卡|慢|不动|加载|白屏|转圈", desc):
            reply += "\n先确认素材包已导入、使用系统浏览器；可刷新或关闭重开页面。不要清除网站数据，避免丢失本地身份。"
        yield event.plain_result(reply)

    @filter.command("bug列表")
    async def list_bugs(self, event: AstrMessageEvent):
        """查看最近bug"""
        event.stop_event()
        data = _load_bugs()
        bugs = data.get("bugs", [])
        if not bugs:
            yield event.plain_result("还没有bug记录，你可以当它运行完美")
            return
        lines = ["最近的bug记录："]
        for b in bugs[-10:]:
            n = b.get("dup_count", 1)
            tag = f" x{n}" if n > 1 else ""
            lines.append(f"#{b['id']} [{b['status']}]{tag} {b['time']} {b['sender']}：{b['desc'][:60]}")
        lines.append(f"共 {len(bugs)} 条，用 /bug清空 可清（管理员）")
        yield event.plain_result("\n".join(lines))

    @filter.command("bug处理")
    async def mark_bug(self, event: AstrMessageEvent, bug_id: str = "", status: str = "已修复"):
        """管理员标记bug状态：/bug处理 编号 已修复"""
        event.stop_event()
        try:
            is_admin = bool(event.is_admin()) or event.unified_msg_origin == self.assistant.get('admin')
        except Exception:
            is_admin = False
        if not is_admin:
            yield event.plain_result("只有管理员能改bug状态哦")
            return
        async with self._bug_lock:
            data = _load_bugs()
            for b in data.get("bugs", []):
                if str(b["id"]) == str(bug_id):
                    b["status"] = status or "已修复"
                    _save_bugs(data)
                    delivery = await self.assistant.community.bug_update(b['id'], b['status'])
                    yield event.plain_result(f"#{b['id']} 已标记为「{b['status']}」\n{delivery}")
                    return
        yield event.plain_result(f"没找到编号 {bug_id} 的bug，/bug列表 看看")

    @filter.command("bug清空")
    async def clear_bugs(self, event: AstrMessageEvent):
        """管理员清空bug列表"""
        event.stop_event()
        try:
            is_admin = bool(event.is_admin())
        except Exception:
            is_admin = False
        if not is_admin:
            yield event.plain_result("只有管理员能清空bug列表哦")
            return
        async with self._bug_lock:
            _save_bugs({"seq": 0, "bugs": []})
        yield event.plain_result("bug列表已清空，干干净净（先 /bug列表 备份想要的内容）")

    # ---------- FAQ 自动答疑 ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def auto_faq(self, event: AstrMessageEvent):
        self._ensure_monitor()
        try:
            if event.is_private_chat():
                return
        except Exception:
            pass
        utility = await self.assistant.utilities.command(event)
        if utility is not None:
            if isinstance(utility, str):
                event.stop_event()
                yield event.plain_result(utility)
            else:
                yield utility
                event.stop_event()
            return
        moderation = self.assistant.utilities.moderation(event)
        if moderation:
            event.stop_event()
            yield event.plain_result(moderation)
            return
        response = await self.assistant.message(event, match_faq)
        if response is not None:
            event.stop_event()
            yield event.plain_result(response)
            return
        if not self.config.get("auto_faq", True):
            return
        msg = (event.message_str or "").strip()
        if re.search(r"排行榜|积分榜|单人榜|多人榜|排名|榜首|第几名", msg):
            # Live rankings must reach the query tool rather than a static FAQ answer.
            return
        if not msg or msg.startswith(("/", "#", ".")):
            return
        lowered = msg.lower()
        if any(k in msg or k in lowered for k in STATUS_KEYS):
            # 要求像一句提问，避免闲聊里提到"服务器"就插话播报
            if self.config.get("status_require_question", True):
                _stripped = msg.strip().rstrip("？?。！!~～ ")
                _looks_q = (any(m in msg for m in _QUESTION_MARKERS)
                            or _stripped in ("服务器状态", "状态查询", "服务器状态查询"))
                if not _looks_q:
                    return
            event.stop_event()
            gid = event.get_group_id() or event.get_sender_id() or "unknown"
            key = (str(gid), "server-status")
            now = time.time()
            if now - self._cooldown.get(key, 0) >= int(self.config.get("cooldown_seconds", 120)):
                self._cooldown[key] = now
                yield event.plain_result(await self._status_text())
            return
        entry = match_faq(msg)
        if not entry:
            return
        if self.config.get("faq_require_question", True):
            if not any(m in msg for m in _QUESTION_MARKERS):
                return
        gid = event.get_group_id() or event.get_sender_id() or "unknown"
        key = (str(gid), entry["id"])
        now = time.time()
        cd = int(self.config.get("faq_cooldown_seconds", 600))
        if now - self._cooldown.get(key, 0) < cd:
            return
        self._cooldown[key] = now
        # 不再原文复读：直出会丢掉人格与颜文字，同一段说明还会对不同的人各发一遍。
        # 改为把权威答案交给模型，由模型按人格作答（见 inject_faq_context 钩子）。
        if self.config.get("faq_answer_mode", "inject") == "direct":
            event.stop_event()
            yield event.plain_result(entry["answer"])
            return
        event.set_extra("wushu_faq_answer", entry["answer"])
        event.set_extra("wushu_faq_title", str(entry.get("title", "") or ""))
        return

    # ---------- 自动监控与主动播报 ----------

    def _announced_get(self, key):
        try:
            return bool(self.assistant.get('announced:' + key, False))
        except Exception:
            return bool(self._announced.get(key))

    def _announced_set(self, key, value):
        self._announced[key] = value
        try:
            self.assistant.put('announced:' + key, bool(value))
        except Exception:
            pass

    def _ensure_monitor(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monitor_loop())
        # 插件重载后即使 initialize 未触发，也让房间播报重新就位
        try:
            self.assistant.rooms.start()
        except Exception:
            pass

    def _targets(self):
        raw = str(self.config.get("broadcast_targets", "") or "")
        return [t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()]

    async def _broadcast(self, text, targets=None):
        sent, failed = [], []
        for umo in (targets if targets is not None else self._targets()):
            try:
                await self.assistant.send(umo, text)
                sent.append(umo)
            except Exception as e:
                # A timed-out send may already have reached QQ; never immediately
                # repeat it through a second API path.
                detail = str(e).strip() or repr(e)
                failed.append("%s（%s: %s）" % (umo, type(e).__name__, detail[:60]))
        from astrbot.api import logger
        if failed:
            logger.warning("[wushu_ops] 播报失败通道: %s" % "; ".join(failed))
        return sent, failed

    async def _monitor_loop(self):
        from astrbot.api import logger
        interval = int(self.config.get("monitor_interval_seconds", 120))
        while True:
            try:
                await asyncio.sleep(interval)
                await self.assistant.utilities.tick()
                await self.assistant.closure.retry()
                await self.assistant.lifecycle.tick()
                await self.assistant.bug_digest(_load_bugs())
                await self._monitor_once()
                await self.assistant.rankings()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[wushu_ops] monitor error: %s" % e)

    async def _monitor_once(self):
        if not self.config.get("monitor_enable", True):
            return
        alerts = []
        results = {}
        for name in STATUS_SERVERS:
            try:
                results[name] = (True, await self.net.api.health(name, timeout=6, retry=FAST_RETRY))
            except NetError as e:
                results[name] = (False, e.user_message())
        # 本地进程级监控（只读代理）
        for port, lname in LOCAL_PORTS:
            try:
                d = await self.net.local.stability(port)
            except NetError:
                continue
            if not d.get("ok"):
                continue
            key = "local-%d" % port
            host = d.get("host", {})
            svc = d.get("service", {})
            h = svc.get("health", {})
            st = {
                "active": bool(d.get("active")),
                "stale": bool(d.get("observationStale")),
                "avail": host.get("availableMB"),
                "build": h.get("build"),
                "restarts": int(svc.get("NRestarts", 0) or 0),
                "uptime": int(h.get("uptimeSec", 0)),
            }
            prev = self._state.get(key)
            if prev is None:
                self._state[key] = st
                continue
            if st["active"] and not prev["active"]:
                alerts.append("✅ %s 本地进程已激活（准入开启）" % lname)
            elif not st["active"] and prev["active"]:
                alerts.append("⚠️ %s 本地进程未激活（准入可能已暂停或进程异常）" % lname)
            if st["stale"] and not prev["stale"]:
                alerts.append("⚠️ %s 状态数据过期（观测进程可能卡住）" % lname)
            if isinstance(st["avail"], int) and st["avail"] < 400 and not self._announced_get(key + "-mem"):
                alerts.append("⚠️ %s 主机可用内存仅 %sMB，注意 OOM 风险" % (lname, st["avail"]))
                self._announced_set(key + "-mem", True)
            elif isinstance(st["avail"], int) and st["avail"] > 600 and self._announced_get(key + "-mem"):
                self._announced_set(key + "-mem", False)
            if st["build"] != prev["build"]:
                alerts.append("🔄 %s 版本变更：v%s → v%s" % (lname, prev["build"], st["build"]))
            if st["uptime"] < prev["uptime"] - 10:
                alerts.append("🔄 %s 发生重启（运行时长重置）" % lname)
            if st["restarts"] > prev["restarts"]:
                alerts.append("⚠️ %s 服务重启次数增加（%s→%s）" % (lname, prev["restarts"], st["restarts"]))
            self._state[key] = st
        for name, (ok, data) in results.items():
            prev = self._state.get(name)
            if not ok:
                self._fails[name] = self._fails.get(name, 0) + 1
                if self._fails[name] == 2 and (prev is None or prev.get("ok")):
                    alerts.append("⚠️ %s 无响应（连续2次检测失败），可能宕机或重启中，持续观察中" % name)
                    self._state[name] = dict(prev or {}, ok=False)
                continue
            self._fails[name] = 0
            st = {"ok": True, "app": str(data.get("app", "?")), "uptime": int(data.get("uptimeSec", 0))}
            if prev is not None and not prev.get("ok"):
                alerts.append("✅ %s 已恢复响应（v%s）" % (name, st["app"]))
            elif prev is not None and prev.get("ok"):
                if st["app"] != prev.get("app"):
                    alerts.append("🔄 %s 版本变更：v%s → v%s（可能已更新上线）" % (name, prev.get("app"), st["app"]))
                elif st["uptime"] < prev.get("uptime", 0) - 10:
                    alerts.append("🔄 %s 发生重启（运行时长重置），当前 v%s" % (name, st["app"]))
            self._state[name] = st
            rooms = data.get("rooms")
            if isinstance(rooms, int) and rooms >= 60 and not self._announced_get(name + "-rooms"):
                alerts.append("⚠️ %s 房间数 %s/64 接近上限，开新房可能提示繁忙" % (name, rooms))
                self._announced_set(name + "-rooms", True)
            elif isinstance(rooms, int) and rooms < 55 and self._announced_get(name + "-rooms"):
                self._announced_set(name + "-rooms", False)
        if alerts and self.config.get("broadcast_enable", True):
            await self._broadcast("【服务器监控】\n" + "\n".join(alerts))

    @filter.command("监控测试")
    async def monitor_test(self, event: AstrMessageEvent):
        """向所有配置通道发送测试播报，验证哪条通道能主动发消息"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result('仅管理员可触发群播报。')
            return
        sent, failed = await self._broadcast("【服务器监控】测试播报：监控运行正常 ✅")
        if failed:
            yield event.plain_result("已发送。成功通道：%s\n失败通道：%s\n（失败的通道无法主动发消息，多为平台限制，如官方机器人通道或个人号掉线）" % ("、".join(sent) or "无", "、".join(failed)))
        else:
            yield event.plain_result("全部通道发送成功 ✅")

    # ---------- 服务器状态 ----------

    async def _status_text(self):
        """带缓存与并发合并的状态查询：30 秒内的重复查询直接复用；
        并发请求合并为同一次探测，避免 N 个人同时查就打 N 轮 HTTP。"""
        async with self._status_lock:
            now = time.time()
            if self._status_cache[1] and now - self._status_cache[0] <= self._status_ttl:
                return self._status_cache[1]
            if self._status_inflight is None:
                self._status_inflight = asyncio.ensure_future(_build_status_text(self.net))
            task = self._status_inflight
        try:
            # 等待期间不持锁，报 bug 等其它操作不受影响
            text = await task
        finally:
            async with self._status_lock:
                if self._status_inflight is task:
                    self._status_inflight = None
        async with self._status_lock:
            self._status_cache = (time.time(), text)
        return text

    @filter.command('待处理问题')
    async def pending_cases(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_private_chat() or not (event.is_admin() or event.unified_msg_origin == self.assistant.get('admin')):
            yield event.plain_result('仅管理员可在私聊中查看。')
            return
        records = self.assistant.execute("SELECT v FROM state WHERE k LIKE 'notice:%' ORDER BY ts DESC LIMIT 10")
        pending = []
        for (raw,) in records:
            try:
                item = json.loads(raw)
            except ValueError:
                continue  # 损坏记录跳过，不影响其余通知展示
            if item.get('status') == 'pending':
                pending.append(item)
        yield event.plain_result('\n\n'.join(r['summary'] for r in pending if r.get('summary')) or '没有待处理通知。')

    @filter.command("服务器状态", alias={"状态查询"})
    async def server_status(self, event: AstrMessageEvent):
        """实时查询游戏服务器状态"""
        event.stop_event()
        self._ensure_monitor()
        yield event.plain_result(await self._status_text())

    @filter.llm_tool(name="get_server_status")
    async def get_server_status(self, event: AstrMessageEvent) -> str:
        """实时查询《卫戍协议》游戏服务器（主服/契约服）的运行状态：是否在线、在线人数、房间数、对局数。当用户询问服务器是否炸了/开服了吗/当前状态时调用"""
        from dafeiyu_runtime.context import read_once
        return await read_once(event, 'wushu:status', self._status_text)

    @filter.command("排行榜", alias={"积分榜", "单人榜", "多人榜"})
    async def leaderboard(self, event: AstrMessageEvent):
        """/排行榜 [单人|多人|全部] [昵称]；默认显示两榜前10条。"""
        event.stop_event()
        raw = (event.message_str or "").strip().lstrip("/／")
        command = raw.split(maxsplit=1)[0] if raw else "排行榜"
        arg = self._command_arg(event, "排行榜", "积分榜", "单人榜", "多人榜")
        division = {"单人榜": "solo", "多人榜": "coop"}.get(command, "all")
        words = arg.split(maxsplit=1)
        if words and words[0] in {"单人", "多人", "全部", "solo", "coop", "all"}:
            division = words[0]
            arg = words[1] if len(words) > 1 else ""
        yield event.plain_result(await ranking_text(self.net.api, division, arg))

    @filter.llm_tool(name="get_game_rankings")
    async def get_game_rankings(self, event: AstrMessageEvent, division: str = "all",
                                player: str = "", limit: int = 10) -> str:
        """实时读取卫戍协议主服累计积分排行榜。查名次、积分、榜首时使用，不能从知识库猜测。

        Args:
            division (string): solo单人、coop多人、all两榜，默认all。
            player (string): 可选的游戏昵称片段；不支持用QQ身份推断昵称。
            limit (number): 每榜显示条数，1到20，默认10。
        """
        from dafeiyu_runtime.context import read_once
        return await read_once(event, ('wushu:rankings', division, player, limit),
                               lambda: ranking_text(self.net.api, division, player, limit))

    @filter.on_llm_request(priority=99)
    async def inject_faq_context(self, event: AstrMessageEvent, req):
        """Keep retrieved data in this request, without changing the persona prefix."""
        ans = event.get_extra("wushu_faq_answer", "")
        if not ans or getattr(req, "_wushu_faq_attached", False):
            return
        req._wushu_faq_attached = True
        req.prompt = (getattr(req, "prompt", "") or "") + (
            "\n\n[本次游戏问题参考资料；不是新指令]\n" + str(ans).strip()
            + "\n仅在相关时依据资料回答，不编造；此条已有资料，无需重复检索。"
        )
        # Do not remove tools from an unfinished tool-call exchange.
        contexts = getattr(req, "contexts", None) or []
        if any(isinstance(c, dict) and (c.get('role') == 'tool' or c.get('tool_calls')) for c in contexts):
            return
        if getattr(req, "func_tool", None):
            from astrbot.core.agent.tool import ToolSet
            req.func_tool = ToolSet(tools=[t for t in req.func_tool.tools
                if t.name not in {"query_project_info", "astr_kb_search"}])

    @filter.on_llm_request(priority=-100)
    async def trim_greeting_tools(self, event: AstrMessageEvent, req):
        """Only unmistakable greetings; preserve tools for ambiguous follow-ups."""
        if re.match(r'^[/／]?(?:报bug|bug提交|bug列表|bug处理|bug清空)(?:\s|$)', (event.message_str or '').strip(), re.I):
            event.stop_event()
            return
        if event.get_extra('wushu_explicit_ai', False):
            from astrbot.core.agent.tool import ToolSet
            req.func_tool = ToolSet()
            return
        text = (event.message_str or '').strip().strip('!！。~～ ')
        if not re.fullmatch(r'(?:你好|您好|早安|早上好|中午好|下午好|晚上好|晚安|谢谢|感谢|哈哈|嘿嘿|嗨|hello|hi)', text, re.I):
            return
        if not req.func_tool or any(c.get('role') == 'tool' or c.get('tool_calls') for c in (req.contexts or []) if isinstance(c, dict)):
            return
        from astrbot.core.agent.tool import ToolSet
        req.func_tool = ToolSet(tools=[t for t in req.func_tool.tools if re.search(r'meme|sticker|emoji|emotion|表情', t.name, re.I)])

    # ---------- LLM 工具（@机器人提问时模型自动调用，检索知识库） ----------

    @filter.llm_tool(name="query_project_info")
    async def query_project_info(self, event: AstrMessageEvent, question: str,
                                 player: str = "", division: str = "all") -> str:
        """查询卫戍协议游戏规则、入口、素材、皮肤、积分成就与排错，也可实时查排行榜名次积分。普通闲聊无需调用，实时人数请用状态查询。

        Args:
            question (string): 用户的问题或想查询的内容
            player (string): 查榜时用户明确提供的游戏昵称片段，不得从QQ身份猜测。
            division (string): 查榜可指定solo单人、coop多人、all两榜，默认all。
        """
        if re.search(r"排行榜|积分榜|单人榜|多人榜|排名|榜首|第几名", question):
            division = "solo" if "单人" in question else "coop" if "多人" in question else division
            return await self.get_game_rankings(event, division, player, 20)
        from astrbot.core.tools.knowledge_base_tools import retrieve_knowledge_base
        try:
            result = await retrieve_knowledge_base(question, event.unified_msg_origin, self.context)
            if result:
                return result
        except Exception:
            pass
        entry = match_faq(question)
        if entry:
            return entry["answer"]
        return "尚未查到可靠资料。请确认主服或契约服、具体操作与现象；不能因为未查到就认定玩家玩错游戏，也不能编造修复结果。"

    @filter.llm_tool(name="send_group_message")
    async def send_group_message(self, event: AstrMessageEvent, text: str) -> str:
        """往游戏群里主动发一条消息。只有在管理员私聊你、明确要求在群里发通知/公告/说明时才调用。

        Args:
            text (string): 要发到群里的完整消息内容
        """
        try:
            if not self._is_ops_admin(event):
                return '仅管理员可发送群公告。'
            if not event.is_private_chat():
                return "只能在私聊里调用发群消息功能。"
            targets = [t for t in self._targets() if ':GroupMessage:' in t]
            if not targets:
                return "没有配置群目标，发不了。"
            from astrbot.api import logger
            sent, failed = 0, []
            for t in targets:
                try:
                    await self.assistant.send(t, text)
                    sent += 1
                except Exception as e:
                    failed.append("%s（%s）" % (t, str(e)[:60]))
            if failed:
                logger.warning("[wushu_ops] 发群消息失败通道: %s" % "; ".join(failed))
            if sent and failed:
                return f"已发到{sent}个群，{len(failed)}个失败：{'; '.join(failed)}"
            return f"已发到{sent}个群。" if sent else "发送失败：%s" % "; ".join(failed)
        except Exception as e:
            return f"发送出错：{e}"

    @filter.command("测试播报")
    async def test_ranking(self, event: AstrMessageEvent):
        """手动触发排行榜播报（管理员测试用）"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result('仅管理员可触发群播报。')
            return
        try:
            await self.assistant.rankings(force=True)
            yield event.plain_result("已触发播报，看看群里收到没。")
        except Exception as e:
            yield event.plain_result(f"播报出错：{e}")

    @filter.command("播报预览")
    async def ranking_preview(self, event: AstrMessageEvent):
        """只生成晨报文案，不发群，用于调试措辞"""
        event.stop_event()
        try:
            text = await self.assistant.rankings(force=True, dry=True)
        except Exception as e:
            yield event.plain_result(f"预览失败：{e}")
            return
        yield event.plain_result(text or "（今天没有可播报的榜单数据）")

    @filter.command("运营帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """插件帮助"""
        event.stop_event()
        yield event.plain_result(
            "🐟 大肥鱼使用指南\n"
            "\n"
            "【报bug】\n"
            "直接发：/报bug 具体描述\n"
            "例：/报bug 契约服进战斗后地图黑一块\n"
            "会自动记录编号，官方定期查看修复。\n"
            "输入 /bug列表 可看大家报过的bug。\n"
            "\n"
            "【自动答疑】（不用@，直接问）\n"
            "· 服务器地址 / 进不去 / 卡顿掉线\n"
            "· 素材怎么导入 / 用什么浏览器\n"
            "· 收费吗 / 怎么加到主屏幕\n"
            "\n"
            "【查询】\n"
            "/排行榜 [单人|多人|全部] [昵称] 查看实时累计积分；也可用 /单人榜、/多人榜。\n"
            "/服务器状态 实时状态；/在线玩家 在线明细（可加“契约服”）。\n"
            "/协议信息 查看服务端版本与 WebSocket 握手延迟。\n"
            "/等待房间 查看还在等人的同盟房间；空房会自动倒计时3秒后播报到群里。\n"
            "\n"
            "【管理】（需管理员，且插件已配置管理令牌）\n"
            "/管理房间 [契约服]\n"
            "/管理踢人 玩家ID [契约服] · /管理踢全部 [契约服]\n"
            "/管理关房 房间号 [force]\n"
            "/管理公告 [普通] 标题|正文 · /管理撤公告\n"
            "/管理重启 [原因]\n"
            "\n"
            "【其他】\n"
            "@大肥鱼 可以聊天、点歌（“来一首xx”）、查资料、锐评。\n"
            "\n"
            "随时发 /运营帮助 查看本指南"
        )

    # ---------- 服务端交互命令（在线 / 协议 / 管理接口） ----------

    def _is_ops_admin(self, event: AstrMessageEvent) -> bool:
        try:
            if bool(event.is_admin()):
                return True
        except Exception:
            pass
        try:
            return event.unified_msg_origin == self.assistant.get('admin')
        except Exception:
            return False

    # 契约服的输入别名，兼容旧的简称
    ALLIANCE_ALIASES = ("契约服", "契约", "联盟")

    @classmethod
    def _split_server(cls, text: str):
        """可选的服务服前缀：`/在线玩家 契约服`、`/管理公告 契约 标题|正文`。"""
        text = (text or "").strip()
        for prefix in cls.ALLIANCE_ALIASES + ("主服",):
            server = SERVER_ALLIANCE if prefix in cls.ALLIANCE_ALIASES else SERVER_MAIN
            if text == prefix:
                return server, ""
            if text.startswith(prefix + " "):
                return server, text[len(prefix):].strip()
        return SERVER_MAIN, text

    @staticmethod
    def _command_arg(event, *names) -> str:
        """取命令后的完整参数串（含空格），不依赖框架的分词行为。"""
        raw = (event.message_str or "").strip().lstrip("/／").strip()
        for name in names:
            if raw.startswith(name):
                return raw[len(name):].strip()
        parts = raw.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @filter.command("在线玩家", alias={"正在玩"})
    async def online_players(self, event: AstrMessageEvent):
        """查询在线玩家：/在线玩家 [契约服]"""
        event.stop_event()
        name, _ = self._split_server(self._command_arg(event, "在线玩家", "正在玩"))
        if not self.net.api.admin_ready():
            # 未配置管理令牌：退回本地只读代理的在线人数（无玩家明细）
            port = 3001 if name == SERVER_ALLIANCE else 3000
            try:
                d = await self.net.local.stability(port)
            except NetError as e:
                yield event.plain_result("%s 在线人数获取失败：%s" % (name, e.user_message()))
                return
            svc = d.get("service") or {}
            count = svc.get("connectedPlayers")
            if count is None:
                yield event.plain_result("%s 暂无在线数据。" % name)
                return
            yield event.plain_result("%s 当前在线 %s 人（本地观测；配置管理令牌后可看玩家明细）" % (name, count))
            return
        try:
            data = await self.net.api.admin_players(name)
        except NetAuthError as e:
            yield event.plain_result("%s：%s" % (name, e.user_message()))
            return
        except NetError as e:
            yield event.plain_result("%s 玩家列表获取失败：%s" % (name, e.user_message()))
            return
        players = data.get("players") or []
        if not players:
            yield event.plain_result("%s 当前没有在线玩家。" % name)
            return
        lines = ["%s 在线 %s 人：" % (name, data.get("count", len(players)))]
        for p in players[:20]:
            bits = []
            if p.get("roomCode"):
                bits.append("房间 %s" % p["roomCode"])
            if p.get("inMatch"):
                bits.append("对局中")
            seen = p.get("lastSeenAgoSec")
            if isinstance(seen, int) and seen > 60:
                bits.append("%d 秒无动作" % seen)
            pid = str(p.get("playerId") or "?")
            lines.append("· %s（%s）%s" % (p.get("name") or "?", pid[:8],
                                          (" · " + " · ".join(bits)) if bits else ""))
        if len(players) > 20:
            lines.append("…另有 %d 人未列出" % (len(players) - 20))
        yield event.plain_result("\n".join(lines))

    @filter.command("协议信息", alias={"协议状态"})
    async def protocol_info(self, event: AstrMessageEvent):
        """HTTP 版本/内容哈希 + 一次 WebSocket 握手与心跳往返"""
        event.stop_event()
        lines = ["🔌 卫戍协议 · 协议信息", ""]
        for name in STATUS_SERVERS:
            try:
                v = await self.net.api.versions(name)
            except NetError as e:
                lines.append("%s ❌ %s" % (name, e.user_message()))
                continue
            content = v.get("content") or {}
            vanilla = str(content.get("vanilla") or "")[:12]
            lines.append("%s · HTTP 协议 v%s · 构建 %s · 内容 %s" % (
                name, v.get("protocol") or "?", v.get("build") or "?", vanilla or "?"))
        lines.append("")
        sock = self.net.socket()
        try:
            welcome = await sock.connect()
            self.net.remember_ws_token(welcome.get("token"))
            await sock.send("ping", c=int(time.monotonic() * 1000))
            latency = sock.latency_ms
            lines.append("WebSocket：已握手 ✅ 以 %s 接入 · 协议 v%s%s" % (
                welcome.get("name") or "?", welcome.get("version") or "?",
                " · 恢复原有会话" if welcome.get("resumed") else ""))
            lines.append("　心跳往返 %s" % ("%dms" % latency if latency is not None else "未测得"))
        except NetError as e:
            lines.append("WebSocket ❌ %s" % e.user_message())
        finally:
            try:
                await sock.close(1000)
            except Exception:
                pass
        yield event.plain_result("\n".join(lines))

    @filter.command("等待房间", alias={"空房", "等待播报"})
    async def waiting_rooms(self, event: AstrMessageEvent):
        """查看等待好友加入的同盟房间：/等待房间 [契约服]"""
        event.stop_event()
        raw = self._command_arg(event, "等待房间", "空房", "等待播报")
        server = None
        for prefix in self.ALLIANCE_ALIASES + ("主服",):
            if raw == prefix:
                server = SERVER_ALLIANCE if prefix in self.ALLIANCE_ALIASES else SERVER_MAIN
                break
        try:
            text = await self.assistant.rooms.describe(server)
        except Exception as e:
            text = "房间状态查询失败：%s" % str(e)[:80]
        yield event.plain_result(text)

    @filter.command("管理房间")
    async def admin_rooms(self, event: AstrMessageEvent):
        """管理员查看房间列表：/管理房间 [契约服]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能查看房间列表。")
            return
        name, _ = self._split_server(self._command_arg(event, "管理房间"))
        try:
            data = await self.net.api.admin_rooms(name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
            return
        except NetError as e:
            yield event.plain_result("%s 房间列表获取失败：%s" % (name, e.user_message()))
            return
        rooms = data.get("rooms") or []
        if not rooms:
            yield event.plain_result("%s 当前没有房间。" % name)
            return
        lines = ["%s 共 %s 个房间：" % (name, data.get("count", len(rooms)))]
        for r in rooms[:20]:
            bits = ["%s" % (r.get("mode") or "?"), str(r.get("difficulty") or "?")]
            bits.append("进行中 #%s" % r.get("round") if r.get("inMatch") else "未开赛")
            bits.append("真人 %s" % r.get("humanCount", 0))
            if r.get("connectedHumans") != r.get("humanCount"):
                bits.append("掉线 %s" % (r.get("humanCount", 0) - r.get("connectedHumans", 0)))
            if r.get("awaySec"):
                bits.append("掉线最久 %d 秒" % int(r["awaySec"]))
            lines.append("· %s %s" % (r.get("code") or "?", " · ".join(bits)))
        if len(rooms) > 20:
            lines.append("…另有 %d 个房间未列出" % (len(rooms) - 20))
        lines.append("用 /管理关房 房间号 关闭房间")
        yield event.plain_result("\n".join(lines))

    @filter.command("管理踢人")
    async def admin_kick(self, event: AstrMessageEvent):
        """管理员踢人：/管理踢人 玩家ID [契约服]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能踢人。")
            return
        name, pid = self._split_server(self._command_arg(event, "管理踢人"))
        if not pid:
            yield event.plain_result("用法：/管理踢人 玩家ID（可用 /在线玩家 查看；前缀“契约服”指定服）")
            return
        if any(alias in pid for alias in self.ALLIANCE_ALIASES):
            name = SERVER_ALLIANCE
        for alias in self.ALLIANCE_ALIASES:
            pid = pid.replace(alias, "")
        pid = pid.strip()
        try:
            data = await self.net.api.admin_kick(pid, name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetError as e:
            yield event.plain_result("%s 踢人失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 已踢出 %s（%s），封禁 %s 秒。" % (
                name, data.get("name") or "?", pid[:8], data.get("banSec", "?")))

    @filter.command("管理踢全部")
    async def admin_kickall(self, event: AstrMessageEvent):
        """管理员踢出全服：/管理踢全部 [契约服]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能踢出全部玩家。")
            return
        name, _ = self._split_server(self._command_arg(event, "管理踢全部"))
        try:
            data = await self.net.api.admin_kickall(name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetError as e:
            yield event.plain_result("%s 操作失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 已踢出 %s 人，封禁 %s 秒。" % (
                name, data.get("kicked", 0), data.get("banSec", "?")))

    @filter.command("管理关房")
    async def admin_close_room(self, event: AstrMessageEvent):
        """管理员关闭房间：/管理关房 房间号 [force] [契约服]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能关闭房间。")
            return
        name, rest = self._split_server(self._command_arg(event, "管理关房"))
        parts = [p for p in rest.replace("，", " ").split() if p]
        force = any(p.lower() in ("force", "强制", "1") for p in parts)
        reserved = ("force", "强制", "1") + self.ALLIANCE_ALIASES
        codes = [p for p in parts if p.lower() not in reserved]
        if not codes:
            yield event.plain_result("用法：/管理关房 房间号 [force]（房间号可用 /管理房间 查看；有在线玩家时需加 force）")
            return
        if any(alias in rest for alias in self.ALLIANCE_ALIASES):
            name = SERVER_ALLIANCE
        code = codes[0]
        for alias in self.ALLIANCE_ALIASES:
            code = code.replace(alias, "")
        code = code.strip()
        try:
            data = await self.net.api.admin_close_room(code, force=force, server=name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetConflict:
            yield event.plain_result("%s 房间 %s 还有在线玩家，确认要关请用：/管理关房 %s force" % (name, code, code))
        except NetError as e:
            yield event.plain_result("%s 关房失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 已关闭房间 %s，移出 %s 人。" % (
                name, data.get("code") or code, data.get("kicked", 0)))

    @filter.command("管理公告")
    async def admin_announce(self, event: AstrMessageEvent):
        """管理员发布公告：/管理公告 [普通] [契约] 标题|正文"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能发布公告。")
            return
        raw = self._command_arg(event, "管理公告")
        if not raw:
            yield event.plain_result("用法：/管理公告 标题|正文\n加 [普通] 前缀 = 普通公告（不强制弹窗）；默认强制弹窗。")
            return
        force = True
        changed = True
        while changed:
            changed = False
            for mark, value in (("[普通]", False), ("[强制]", True)):
                if raw.startswith(mark):
                    force, raw, changed = value, raw[len(mark):].strip(), True
        name = SERVER_MAIN
        if raw.startswith("主服 "):
            raw = raw.split(" ", 1)[1].strip()
        else:
            for alias in self.ALLIANCE_ALIASES:
                if raw.startswith(alias + " "):
                    name, raw = SERVER_ALLIANCE, raw.split(" ", 1)[1].strip()
                    break
        if "|" in raw:
            title, content = raw.split("|", 1)
        else:
            title, content = "", raw
        try:
            data = await self.net.api.admin_announce(title, content, force=force, server=name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetError as e:
            yield event.plain_result("%s 公告发布失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 公告已发布（%s）%s" % (
                name, "强制弹窗" if force else "普通公告",
                "，强制公告编号 %s" % data.get("forceId") if data.get("forceId") else ""))

    @filter.command("管理撤公告")
    async def admin_announce_clear(self, event: AstrMessageEvent):
        """管理员撤回强制公告：/管理撤公告 [契约服]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能撤回公告。")
            return
        name, _ = self._split_server(self._command_arg(event, "管理撤公告"))
        try:
            data = await self.net.api.admin_announce_clear(name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetError as e:
            yield event.plain_result("%s 撤回失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 已撤回 %s 条强制公告（普通公告保留）。" % (
                name, data.get("removed", "?")))

    @filter.command("管理重启")
    async def admin_restart(self, event: AstrMessageEvent):
        """管理员重启服务端：/管理重启 [原因]"""
        event.stop_event()
        if not self._is_ops_admin(event):
            yield event.plain_result("只有管理员能重启服务端。")
            return
        name, reason = self._split_server(self._command_arg(event, "管理重启"))
        try:
            data = await self.net.api.admin_restart(reason or "bot", name)
        except NetAuthError:
            yield event.plain_result("未配置管理令牌（admin_token），管理指令不可用。")
        except NetConflict:
            yield event.plain_result("%s 未启用重启（服务端 SP_ALLOW_RESTART 未设为 1）。" % name)
        except NetError as e:
            yield event.plain_result("%s 重启失败：%s" % (name, e.user_message()))
        else:
            yield event.plain_result("%s 已请求重启（%s），进行中的对局会回到最近一次休整期存档，玩家 60 秒内刷新可接回。" % (
                name, data.get("reason") or reason or "bot"))

    async def terminate(self):
        try:
            await self.assistant.rooms.stop()
        except Exception:
            pass
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.assistant.close()
        try:
            await self.net.close()
        except Exception:
            pass
