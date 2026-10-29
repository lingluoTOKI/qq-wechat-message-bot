"""大肥鱼功能说明书：/功能 一键列出她全部能做的事；/功能 语音 只看某一类。

为什么单独做成一个小插件：
  · 说明书横跨好几个插件（语音、翻唱、游戏、记忆、用量、群管…），
    放进任何一个都会随那个插件的改动一起被动
  · 命令直接返回文本、**不经过大模型 → 0 token**
  · 改文案只改这一个文件，不碰任何功能代码

对齐说明：中文是双宽字符、英文/斜杠是单宽，手工数空格一定会歪。
所以纯文本版下面用 _line() 按**显示宽度**自动补空格，改文案不用再数空格。

渲染规则（1.3.0 起）：
  · QQ 官方机器人开了 markdown（平台配置 use_markdown=true），走原生 markdown 排版：
      # 总标题 / ## 分类标题 / - 列表 / `命令` 行内代码
  · **文案只有一份**（SECTIONS），两个渲染器共用；md 与纯文本的差别只在标记上。
  · 自动回退：平台不是 qq_official、或 md 文本超过 MD_LIMIT 时，发纯文本对齐版。
    （qq_official 发送被拒时框架自己会回退到 content 模式，那时会看到原始标记，
      所以 md 标记只用最克制的几种，不做表格、不做嵌套。）

维护约定（改这个文件前先读）：
  · 这里只写**线上真的注册了的命令**。加功能时同步补这一份，别凭印象写。
  · 命令写成 /命令 参数，参数用尖括号或直白中文；写不下的细节放说明里。
  · 面向群友，不写实现细节（推理后端、token 成本之类）。
  · 命令里出现 <内容> / <歌名> 这类尖括号，md 版必须包在反引号里，
    否则会被当成 HTML 标签吞掉。
  · 文案里别用 * _ ` 这几个字符（会和 markdown 语法冲突）。

变更记录：
  1.6.1  2026-10-29  新增历史图片瘦身（img_slim）：3 轮以前的内嵌大图换成一行文字，
                     当前/近期图片照常发送。
  1.6.0  2026-10-28  新增「自检」分类：每项能力写清怎么用、怎么判定真的通了；
                    记忆说明补「会主动去翻记忆库」。
  1.4.0  2026-10-28  记忆类拆成两套：新增 /记忆 系列（聊天原话留档 + 人物档案），
                     与原有 /lmem（提炼后的语义记忆）分开说明，避免混为一谈。
  1.3.0  2026-10-27  QQ 放开 markdown 后改走原生 md 排版；保留纯文本渲染器做回退。
  1.2.0  2026-10-27  7 类扩到 9 类（点歌翻唱 / 音乐 / 记忆 / 用量拆分），
                     新增「说明行」机制（命令留空 = 整行说明）。
  1.1.0              「其它」类补随口聊天 / 链接解析 / 表情包。
  1.0.0              首版，7 类功能清单。
"""
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from . import ctxbudget as _cb
from .img_slim import slim_history_images

# 说明行：命令与描述从那第 COL 个"半宽"位置开始，保证全部左对齐（纯文本版用）
COL = 22

# md 版文本上限：超了就退回纯文本，避免平台侧截断把格式切烂。
# QQ 官方 markdown content 上限约 4000 字符，留出安全余量。
MD_LIMIT = 3600


def _dw(s: str) -> int:
    """估算显示宽度：CJK/全角算 2，其余算 1。"""
    w = 0
    for ch in s:
        o = ord(ch)
        if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF
                or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF
                or 0xFE30 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60
                or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD):
            w += 2
        else:
            w += 1
    return w


def _line(cmd: str, desc: str, col: int = COL) -> str:
    pad = max(1, col - _dw(cmd))
    return cmd + " " * pad + desc


# 分类：(分类名, 小标题, [(命令, 说明)], [别名])
# 命令为空串表示这是一条说明行（不带命令）。
SECTIONS = [
    ("语音", "让我开口说话", [
        ("/说 <内容>", "用洛天依的声音念这句（本地合成，失败自动换备用嗓子）"),
        ("/语音开", "之后的回复带语音；/语音关 换回纯文字"),
        ("/语音模式 只语音", "只发语音；/语音模式 文字加语音 两边都发"),
        ("/语音状态", "看开没开、现在用哪档嗓子"),
        ("/语音测试", "依次测本地 / MiMo / Edge 三档通不通"),
        ("", "语音开着时，聊天也会顺手带一条语音"),
    ], ["说", "tts", "朗读", "开口"]),
    ("点歌翻唱", "把歌换成我的声音", [
        ("/点歌 <歌名>", "搜歌 → 回列表选序号 → 自动下载后翻唱"),
        ("/翻唱", "我先等着，180 秒内把音频/语音条发进来"),
        ("/翻唱 + 同条带音频", "一步到位，不用另发"),
        ("", "直接发音频或语音条也能换音色（视频里的音轨也行）"),
        ("/发送方式 语音", "结果怎么发：语音 / 文件 / 自动"),
        ("", "长歌自动压成 mp3 发文件，不在群里刷屏"),
    ], ["翻唱", "点歌", "唱歌", "sing", "cover"]),
    ("音乐", "只想听原唱", [
        ("/搜索歌曲 <关键词>", "搜歌并回序号，不下载也不翻唱"),
    ], ["听歌", "搜歌", "歌曲", "music"]),
    ("游戏", "服务器那边的事", [
        ("/服务器状态", "在线人数、房间、玩家列表、延迟"),
        ("/报bug <描述>", "记一笔问题单，会回编号"),
        ("/bug列表", "看大家报过的 bug 和处理进度"),
        ("", "进不去 / 卡顿掉线 / 素材导入这类问题，直接问我就行"),
    ], ["雾锁", "服务器", "开服", "game", "bug"]),
    ("记忆", "我还记得什么", [
        ("/记忆 状态", "我存了多少聊天记录、认识多少人"),
        ("/记忆 找 <名字>", "我对这个人的印象：聊过多少、记得他什么"),
        ("", "聊天原话都留着，问我「上次说过什么」我去翻这些；同步/提炼由我自动做"),
        ("", "问我「你还记得…」我会主动去记忆库里搜，不再只靠当前上下文"),
        ("/lmem status", "记忆库现在存了多少东西"),
        ("/lmem search <词>", "翻我记着的相关的事"),
        ("/lmem forget <词>", "让我忘掉某件事"),
        ("/lmem summarize", "把最近聊的压成一条记忆"),
        ("/lmem cleanup", "清理过期、低价值记忆（管）"),
        ("/lmem reset", "清空重来（管，慎用）"),
        ("/lmem help", "全部记忆命令（含重建索引 / 图谱 / 网页端）"),
    ], ["记性", "lmem", "记忆库", "memory"]),
    ("群管", "管理员用", [
        ("/禁言 <@人> <分钟>", "禁言；/解禁 解除；/全体禁言 全群静音"),
        ("/撤回最新消息", "撤最近一条；/批量撤回 <条数> 一次撤多条"),
        ("/踢人 <@人>", "请出群；/批量踢人 批量处理"),
        ("/发公告 <内容>", "群公告；/公告列表 / /删公告 <编号>"),
        ("/添加违禁词 <词>", "自动拦截；/查看违禁词 / /删除违禁词"),
        ("/群统计 或 /字数统计", "活跃度与发言字数排行"),
        ("/成员列表 /搜索成员", "查人、看谁在群里"),
        ("/设置名片 <@人>", "改群昵称；/群名 /头衔 改群名与头衔"),
        ("/加群方式 /自动审核", "入群设置与自动审批"),
        ("/运营帮助", "群管命令完整清单（三十多条都在那）"),
    ], ["管理", "运营", "管理员", "admin"]),
    ("B站", "追更 UP 主", [
        ("/bili 订阅 <UP>", "有新动态就搬到群里"),
        ("/bili 订阅列表", "看订阅了谁；/bili 取消订阅 <UP> 取关"),
        ("/bili 测试", "立刻拉一次，看通不通"),
        ("/bili 状态", "订阅数量和上次检查时间"),
    ], ["b站", "bili", "动态", "up"]),
    ("用量", "花了多少钱", [
        ("/用量", "今日 token、花费、缓存命中率"),
    ], ["花费", "token", "cost", "费用"]),
    ("自检", "怎么确认我真的会了", [
        ("说「你还记得…」", "我去长期记忆库里搜；答出具体细节才算通"),
        ("说「记住：…」", "写进长期记忆；隔几轮再问一次，应能复述"),
        ("说「上次谁说过…」", "翻群聊原话留档；只答大概、说不出人就是没通"),
        ("说「XX 是什么人」", "人物档案：和他聊过多少、记得他什么"),
        ("发条 B 站链接", "应回标题和简介；原样复读说明解析没触发"),
        ("/点歌 <歌名>", "先出候选列表，选序号后发出语音才算通"),
        ("/翻唱", "180 秒内把音频发进来，我回成品文件才算通"),
        ("/ctxbudget", "看工具数：正常 27 个，变了就是工具集又漂了（管）"),
        ("", "这些都是不烧 token 或只走一轮的测法，测完发 /用量 对账"),
    ], ["测试", "自测", "测试说明", "check", "verify"]),
    ("其它", "不用记命令", [
        ("直接说话", "叫「大肥鱼」或 @我，聊天、问问题、查资料都行"),
        ("发链接给我", "B站 / 抖音 / 小红书等自动解析成图文"),
        ("/关闭解析", "不想让我解析链接时用；/开启解析 恢复"),
        ("", "表情包我自己会挑着发，也会在群里主动搭话"),
    ], ["其他", "杂项", "闲聊", "misc"]),
]

ALIAS = {}
for _name, _title, _items, _aliases in SECTIONS:
    ALIAS[_name] = _name
    for _a in _aliases:
        ALIAS[_a] = _name


# ---------------- 渲染 ----------------
def _row_md(cmd: str, desc: str) -> str:
    """markdown 版一行：命令包行内代码（也顺便挡住 <内容> 被当 HTML）。"""
    return "- `%s` %s" % (cmd, desc) if cmd else "- " + desc


def _row_plain(cmd: str, desc: str) -> str:
    return "  " + (_line(cmd, desc, COL) if cmd else "· " + desc)


def _render_section(idx: int, md: bool = False) -> str:
    name, title, items, _ = SECTIONS[idx]
    if md:
        lines = ["## %s · %s" % (name, title), ""]
    else:
        lines = ["哼，【%s】这类本小姐会这些：（<ゝω・）☆" % name, ""]

    for cmd, desc in items:
        lines.append(_row_md(cmd, desc) if md else _row_plain(cmd, desc))

    if name == "群管":
        lines.append("")
        if md:
            lines.append("> 以上都要群管理权限，没列的都在 `/运营帮助` 里。")
        else:
            lines.append("  以上都要群管理权限；没列的都在 /运营帮助 里。")

    lines.append("")
    if md:
        lines.append("看全部 `/功能`　·　只看一类 `/功能 <分类名>`")
    else:
        lines.append("发 /功能 看全部；发 /功能 <分类名> 只看某一类。")
    return "\n".join(lines)


def _render_all(md: bool = False) -> str:
    if md:
        lines = [
            "# 大肥鱼 · 功能说明书",
            "",
            "命令直接回文字，**不烧 token**。每条都能单独查：`/功能 <分类名>`",
            "",
        ]
        for name, title, items, _ in SECTIONS:
            lines.append("## %s · %s" % (name, title))
            for cmd, desc in items:
                lines.append(_row_md(cmd, desc))
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("懒得记命令？用大白话跟我说，我听得懂。")
        lines.append("分类：" + "　·　".join("`%s`" % s[0] for s in SECTIONS))
        return "\n".join(lines)

    lines = [
        "哼，居然要本小姐自报家门？（<ゝω・）☆",
        "下面是全部本事，命令直接回文字、不烧 token：",
        "",
    ]
    for name, title, items, _ in SECTIONS:
        lines.append("—— %s · %s ——" % (name, title))
        for cmd, desc in items:
            lines.append(_row_plain(cmd, desc))
        lines.append("")
    lines.append("懒得记？用大白话跟我说就行，我听得懂。")
    lines.append("只看一类：")
    lines.append("  " + " · ".join("/功能 " + s[0] for s in SECTIONS))
    return "\n".join(lines)


@register(
    "astrbot_plugin_dafeiyu_help",
    "ops",
    "大肥鱼功能说明书：/功能 列出全部功能，/功能 分类 只看某一类（不经过模型，0 token）",
    "1.6.1",
)
class DafeiyuHelpPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        # 诊断用：每个会话最近一轮请求的构成快照（只存哈希与长度）
        self._cb_sessions = {}

    # ---------- 省钱：历史大图不再每轮重发 ----------

    @filter.on_llm_request(priority=98)
    async def slim_old_images(self, event: AstrMessageEvent, req):
        """把 3 轮以前的历史图片换成一行文字（当前/近期图片原样保留）。

        实测一张 223KB 的内嵌图单条消息就 ~56k token，且会每轮重发；
        省略后上下文回落，也不再周期性顶到 64k 触发摘要压缩。
        """
        try:
            removed, chars = slim_history_images(req)
            if removed:
                logger.info(
                    "[img_slim] 省略历史图片 %d 张 / %d 字符（%s）"
                    % (removed, chars, getattr(event, "unified_msg_origin", "") or "")
                )
        except Exception as e:
            logger.debug("[img_slim] 跳过：%s" % str(e)[:120])

    def _arg(self, event):
        s = (event.message_str or "").strip()
        for c in ("功能", "帮助", "菜单", "能干啥", "会什么", "help", "说明书"):
            for pre in ("/", "／", "", "#"):
                t = pre + c
                if s.startswith(t):
                    return s[len(t):].strip().lstrip("/／").strip()
        return s

    def _reply(self, event, render):
        """render(md) -> str。QQ 官方走 markdown，其余平台 / 超长退回纯文本。"""
        try:
            md_ok = event.get_platform_name() == "qq_official"
        except Exception:
            md_ok = False
        if md_ok:
            text = render(True)
            if len(text) <= MD_LIMIT:
                return event.plain_result(text).use_markdown(True)
        return event.plain_result(render(False))

    # ---------- 真实版 /ctxbudget + 未知命令守门人 ----------
    # 注意：实现放在 ctxbudget.py，但**装饰器必须写在本文件（插件自身模块）**，
    # 否则框架会把它当成别的模块的 handler，插件自己永远不会被调度到。

    @filter.on_llm_request(priority=200000)
    async def dg_ctxbudget_capture(self, event: AstrMessageEvent, req):
        _cb.capture(event, req)

    @filter.on_llm_request(priority=200001)
    async def dg_ctxbudget_gate(self, event: AstrMessageEvent, req):
        await _cb.gate(event, req)

    @filter.command("ctxbudget")
    async def dg_ctxbudget_cmd(self, event: AstrMessageEvent):
        await _cb.ctxbudget_cmd(event)

    @filter.command("功能", alias={"帮助", "菜单", "能干啥", "会什么", "help", "说明书"})
    async def help_cmd(self, event: AstrMessageEvent):
        """列出大肥鱼的全部功能。用法：/功能　或　/功能 语音"""
        arg = self._arg(event)
        if arg:
            key = ALIAS.get(arg)
            if key:
                idx = next(i for i, s in enumerate(SECTIONS) if s[0] == key)
                yield self._reply(event, lambda md: _render_section(idx, md))
                return
            # 模糊匹配：包含关键字
            for i, (name, _t, _it, _al) in enumerate(SECTIONS):
                if arg in name or name in arg or any(arg in a for a in _al):
                    yield self._reply(event, lambda md: _render_section(i, md))
                    return
        yield self._reply(event, _render_all)
