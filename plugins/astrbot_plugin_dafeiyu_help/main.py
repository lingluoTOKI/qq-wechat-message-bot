"""大肥鱼说明书：常用入口、分类帮助和完整命令列表。

文案统一维护于SECTIONS；QQ使用简洁Markdown，其余平台用逐行文本。
说明书直接回复，不调用模型。上下文诊断与历史图片处理保持独立。
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


# 分类：(分类名, 小标题, [(命令, 说明[, 补充])], [别名])
# 条目三种写法：
#   ("#", "小标题")            分块标题
#   (命令, 说明)               一行命令
#   (命令, 说明, 补充)         命令 + 分类页里多一行备注
# 命令为空串表示这是一条说明行（不带命令）。
SECTIONS = [
    ("入门", "第一次用先看这几条", [
        ("@大肥鱼 问题", "群里 @ 我，或私聊直接说话"),
        ("/功能 全部", "一次看全部命令列表"),
        ("/功能 语音", "只看某一类，类别名见下方列表"),
        ("", "说明书是直接回复，不调用模型、不花钱"),
        ("", "群聊有每日额度和高峰休眠，规则见「用量」"),
    ], ["新手", "上手", "start", "guide"]),

    ("语音", "朗读与语音回复", [
        ("#", "基础"),
        ("/说 你好", "把这段文字念出来，回一条语音",
         "支持中英混排；带链接或长段代码会跳过"),
        ("/语音开", "之后每条回复自动带语音",
         "会让回复延迟几秒，赶时间可先关掉"),
        ("/语音关", "关闭自动语音", "已发出的语音不受影响"),
        ("#", "进阶"),
        ("/语音模式 只语音", "只发语音，不再发文字",
         "另可选 文字加语音：文字为主、附一条语音"),
        ("/语音状态", "查看当前开关、模式和音色档位", None),
        ("", "音色跟我的设定走；本地引擎忙时会自动降级"),
    ], ["说", "tts", "朗读", "读"]),

    ("点歌翻唱", "把歌曲换成我的音色", [
        ("#", "流程"),
        ("/点歌 歌名", "搜索 → 回复序号 → 下载并翻唱",
         "翻唱比原唱慢很多，通常要等一会儿"),
        ("", "回复 0 取消选歌；超时未回复也会自动取消"),
        ("/发送方式 语音", "可选 语音 / 文件 / 自动",
         "文件适合保存，语音适合直接听"),
        ("#", "自己提供音频"),
        ("/翻唱", "按提示发送一段音频，我用音色重唱",
         "也可在同一条消息里直接附上音频"),
        ("", "时长过长会失败；建议 30 秒以内的片段"),
    ], ["翻唱", "点歌", "唱歌", "sing", "cover"]),

    ("音乐", "播放原唱", [
        ("/搜索歌曲 歌名", "搜索 → 回复序号 → 发原唱音频",
         "搜不到时换个写法，或改用具体歌名 + 歌手"),
        ("", "回复「序号 下载」收文件；回复「取消」结束"),
        ("", "同一人再次搜索或点歌，以最近一次为准"),
    ], ["听歌", "搜歌", "歌曲", "music"]),

    ("游戏", "卫戍协议", [
        ("#", "查分"),
        ("/排行榜 单人 TOKI", "按昵称查累计积分",
         "昵称要写游戏里显示的完整名字"),
        ("/排行榜", "查看总榜", None),
        ("#", "服务器"),
        ("/服务器状态", "主服与契约服是否在线", None),
        ("/在线玩家", "当前在线明细", None),
        ("#", "反馈"),
        ("/报bug 描述", "提交问题并返回编号",
         "附上报错时间与操作步骤，处理更快"),
        ("/bug列表", "查看问题与处理进度", None),
        ("/运营帮助", "运营与维护说明", None),
    ], ["卫戍", "雾锁", "服务器", "开服", "game", "bug", "排行榜"]),

    ("记忆", "聊天归档与人物资料", [
        ("#", "查询"),
        ("/记忆 状态", "归档数量与最近更新时间", None),
        ("/记忆 找 名字", "查这个人的资料和相关片段",
         "资料来自群聊与私聊的公开发言"),
        ("/lmem search 关键词", "搜索提炼后的长期记忆",
         "搜的是摘要，不是逐字原话；用短关键词更准"),
        ("#", "维护"),
        ("/lmem help", "查看其他记忆命令", None),
        ("", "记忆跨会话共享，并标注来源群或私聊"),
        ("", "旧摘要可能缺来源标签；每天 0 点整理前一天"),
    ], ["记性", "lmem", "记忆库", "memory"]),

    ("B站", "订阅 UP 主动态", [
        ("/bili 订阅 UID", "为当前会话订阅",
         "UID 是数字，不是昵称；可在 UP 主页链接里找到"),
        ("/bili 订阅列表", "查看本会话的订阅", None),
        ("/bili 取消订阅 UID", "移除订阅", None),
        ("/bili 状态", "运行状态与异常投递数量", None),
        ("/bili 测试", "立即检查一次", "只推新动态，不会补推历史"),
    ], ["b站", "bili", "动态", "up"]),

    ("用量", "余额、花费与规则", [
        ("#", "查询"),
        ("/用量", "余额、今日 Token、费用参考、预算剩余",
         "费用参考按官方单价估算，不是最终账单"),
        ("/账单", "同一份统计的精简版", None),
        ("/用量 7", "近 7 天用量", "预算剩余仍按今日与本月"),
        ("#", "规则"),
        ("", "余额查询时刷新，最多缓存 60 秒"),
        ("", "群聊每个群每天 ¥0.5；用完当天不再回复，次日 0 点恢复"),
        ("", "工作日 9-12 点、14-18 点为高峰，群聊休眠；12 点、18 点自动恢复"),
        ("", "私聊不受额度与休眠限制；休眠期间的消息不补答"),
        ("", "额度用尽后，规则查询和说明书仍然可用"),
    ], ["余额", "账单", "花费", "token", "cost", "费用", "额度", "休眠", "高峰", "预算"]),

    ("管理", "管理员与诊断", [
        ("/ctxbudget", "当前会话上下文构成（仅管理员）",
         "只读快照，0 token；普通成员发送无回复"),
        ("/关闭解析", "关闭当前会话的自动链接解析（管理员）", None),
        ("/开启解析", "恢复自动解析", None),
        ("", "禁言、踢人等群管插件当前未启用"),
    ], ["群管", "运营", "管理员", "admin", "自检", "测试", "check"]),

    ("其它", "聊天与链接", [
        ("@大肥鱼 + 问题", "聊天、问答、查资料",
         "回答会参考当前会话的历史与记忆"),
        ("发送链接", "支持的平台会尝试解析",
         "需要登录或有平台限制时，解析会失败或只给标题"),
        ("发送图片", "我只看最近几轮", "更早的历史图片会被省略，不再重复占上下文"),
        ("", "我不会执行 shell、写文件或读取任意路径，这是刻意设计"),
    ], ["其他", "闲聊", "misc"]),

    ("常见问题", "FAQ", [
        ("群里不理我", "额度用完或高峰休眠，查「用量」看剩余"),
        ("语音没声音", "可能合成超时或引擎忙，稍后重试即可"),
        ("点歌搜不到", "音源没有该曲目，换写法或换一首"),
        ("记忆说的不准", "旧摘要可能缺来源，用 /记忆 找 名字 核对"),
        ("想加功能", "找 TOKI，别在群里刷屏"),
    ], ["faq", "问题"]),
]


ALIAS = {}
for _name, _title, _items, _aliases in SECTIONS:
    ALIAS[_name.lower()] = _name
    for _a in _aliases:
        ALIAS[_a.lower()] = _name


def _is_head(item) -> bool:
    return len(item) == 2 and item[0] == "#"


# ---------------- 渲染 ----------------
def _row_md(cmd: str, desc: str) -> str:
    """markdown 版一行：命令包行内代码（也顺便挡住 <内容> 被当 HTML）。"""
    return "- `%s` %s" % (cmd, desc) if cmd else "- " + desc


def _row_plain(cmd: str, desc: str) -> str:
    return (cmd + ("：" + desc if desc else "")) if cmd else "· " + desc


def _render_section(idx: int, md: bool = False) -> str:
    name, title, items, _ = SECTIONS[idx]
    if md:
        lines = ["## %s · %s" % (name, title), ""]
    else:
        lines = ["%s · %s" % (name, title), ""]

    for item in items:
        if _is_head(item):
            if md:
                lines.append("")
                lines.append("### %s" % item[1])
            else:
                lines.append("【%s】" % item[1])
            continue
        cmd, desc = item[0], item[1]
        note = item[2] if len(item) > 2 else None
        lines.append(_row_md(cmd, desc) if md else _row_plain(cmd, desc))
        if note:
            lines.append(("  - %s" % note) if md else "    " + note)

    lines.append("")
    if md:
        lines.append("返回 `/功能`　·　完整列表 `/功能 全部`")
    else:
        lines.append("返回 /功能；完整列表 /功能 全部。")
    return "\n".join(lines)


def _render_full(md: bool = False) -> str:
    """完整列表：所有命令 + 一行说明；更细的备注留在分类页。"""
    if md:
        lines = [
            "# 大肥鱼 · 完整命令列表",
            "",
            "带补充备注的用法看 `/功能 <分类>`。",
            "说明书不调用模型；语音、翻唱等具体功能会调用付费服务。",
            "",
        ]
        for name, title, items, _ in SECTIONS:
            lines.append("## %s · %s" % (name, title))
            for item in items:
                if _is_head(item):
                    lines.append("### %s" % item[1])
                else:
                    lines.append(_row_md(item[0], item[1]))
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("返回 `/功能` 查看常用入口。")
        return "\n".join(lines)

    lines = [
        "大肥鱼 · 完整命令列表",
        "带补充备注的用法看 /功能 分类名。",
        "说明书不调用模型；具体功能可能收费。",
        "",
    ]
    for name, title, items, _ in SECTIONS:
        lines.append("—— %s · %s ——" % (name, title))
        for item in items:
            if _is_head(item):
                lines.append("【%s】" % item[1])
            else:
                lines.append(_row_plain(item[0], item[1]))
        lines.append("")
    lines.append("返回 /功能 查看常用入口。")
    return "\n".join(lines)


def _render_all(md: bool = False) -> str:
    title = "# 大肥鱼说明书" if md else "大肥鱼说明书"
    rows = [
        ("/功能 全部", "全部命令列表"),
        ("/用量", "余额、今日用量与预算"),
        ("/排行榜 单人 TOKI", "按昵称查卫戍积分"),
        ("/搜索歌曲 歌名", "听原唱"),
        ("/点歌 歌名", "听翻唱"),
        ("/说 你好", "文字转语音"),
        ("/记忆 找 名字", "查人物资料"),
    ]
    row = _row_md if md else _row_plain
    lines = [title, "", "常用入口"] + [row(c, d) for c, d in rows]
    lines += ["", "按分类查看"]
    for name, _, _, _ in SECTIONS:
        lines.append(row("/功能 " + name, ""))
    lines += ["", row("/功能 全部", "全部命令列表"),
              "说明书不调用模型；具体功能可能收费。群聊额度与高峰休眠见「用量」。"]
    return "\n".join(lines)


@register(
    "astrbot_plugin_dafeiyu_help",
    "ops",
    "常用命令与分类帮助：/功能 看常用入口，/功能 分类 看详细用法，/功能 全部 看完整命令列表。",
    "1.8.0",
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
        event.stop_event()
        allowed = _cb._is_admin(event)
        logger.info(f"[ctxbudget] command_handler admin={allowed}")
        if allowed:
            yield event.plain_result(_cb.ctxbudget_text(event))

    @filter.command("功能", alias={"帮助", "菜单", "能干啥", "会什么", "说明书"})
    async def help_cmd(self, event: AstrMessageEvent):
        """列出大肥鱼的全部功能。用法：/功能　或　/功能 语音"""
        event.stop_event()
        arg = self._arg(event).strip().lower()
        if arg in {"全部", "所有", "all"}:
            yield self._reply(event, _render_full)
            return
        if arg:
            key = ALIAS.get(arg)
            if key:
                idx = next(i for i, s in enumerate(SECTIONS) if s[0] == key)
                yield self._reply(event, lambda md: _render_section(idx, md))
                return
            # 模糊匹配：包含关键字
            for i, (name, _t, _it, _al) in enumerate(SECTIONS):
                if arg in name.lower() or name.lower() in arg or any(arg in a.lower() for a in _al):
                    yield self._reply(event, lambda md: _render_section(i, md))
                    return
        if arg:
            yield self._reply(event, lambda md: "未找到分类“" + arg[:30] + "”。\n\n" + _render_all(md))
            return
        yield self._reply(event, _render_all)
