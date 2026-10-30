"""大肥鱼说明书：常用入口、分类帮助和完整命令列表。

文案统一维护于manual.py的SECTIONS；QQ使用简洁Markdown，其余平台用逐行文本。
说明书直接回复，不调用模型。上下文诊断与历史图片处理保持独立。
"""
import importlib
import re
from pathlib import Path
import astrbot.api.message_components as Comp

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
PAGE_LIMIT = 2400


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
from . import manual as _manual
importlib.reload(_manual)
SECTIONS = _manual.SECTIONS


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
            "说明书与规则查询不调用聊天模型；聊天和部分辅助功能按实际服务计费。",
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
        "说明书不调用聊天模型；具体功能按实际服务计费。",
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
        ("/搜索 关键词", "联网查资料"),
        ("/记忆 画像", "查看自己的资料并纠错"),
        ("/记忆 生日", "生日名单与零点祝福"),
        ("/考研 今天", "50天计划与Notion打卡"),
    ]
    row = _row_md if md else _row_plain
    lines = [title, "", "常用入口"] + [row(c, d) for c, d in rows]
    lines += ["", "按分类查看"]
    for name, _, _, _ in SECTIONS:
        lines.append(row("/功能 " + name, ""))
    lines += ["", row("/功能 全部", "全部命令列表"),
              "说明书不调用聊天模型；具体功能按实际服务计费。群聊额度与高峰休眠见「用量」。"]
    return "\n".join(lines)


def _full_pages(md=False):
    """Split only between complete lines, leaving room for navigation."""
    pages=[];current=[];length=0
    for line in _render_full(md).splitlines():
        if current and length+len(line)+1>PAGE_LIMIT:
            pages.append('\n'.join(current).strip());current=[];length=0
        current.append(line);length+=len(line)+1
    if current:pages.append('\n'.join(current).strip())
    return pages


def _render_full_page(page=1, md=False):
    pages=_full_pages(md)
    if not 1<=page<=len(pages):
        return f'完整列表共{len(pages)}页，请输入 /功能 全部 1 至 /功能 全部 {len(pages)}。'
    title=f'大肥鱼完整列表 · 第{page}/{len(pages)}页'
    navigation=[]
    if page>1:navigation.append(f'上一页：/功能 全部 {page-1}')
    if page<len(pages):navigation.append(f'下一页：/功能 全部 {page+1}')
    navigation.append('详细步骤：/功能 分类名；目录：/功能')
    return title+'\n\n'+pages[page-1]+'\n\n'+'\n'.join(navigation)


@register(
    "astrbot_plugin_dafeiyu_help",
    "ops",
    "常用命令与分类帮助：/功能 看常用入口，/功能 分类 看详细用法，/功能 全部 看完整命令列表。",
    "1.10.1",
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
        s=re.sub(r'^(?:(?:\[At:[^\]]*\]|\[CQ:at,[^\]]*\]|<@!?[^>]+>)\s*)+','',s).strip()
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

    def _file_result(self,event,extension='pdf'):
        extension='txt' if extension=='txt' else 'pdf'
        name='大肥鱼完整说明书.'+extension
        path=Path(__file__).with_name('resources')/name
        if not path.is_file():
            return event.plain_result('说明书附件暂不可用，请用 /功能 分页 1 查看完整文字版。')
        return event.chain_result([
            Comp.Plain('完整说明书（'+extension.upper()+'）。若附件未显示，可用 /功能 分页 1 查看。'),
            Comp.File(name=name,file=str(path)),
        ])

    # ---------- 真实版 /ctxbudget + 未知命令守门人 ----------
    # 注意：实现放在 ctxbudget.py，但**装饰器必须写在本文件（插件自身模块）**，
    # 否则框架会把它当成别的模块的 handler，插件自己永远不会被调度到。

    @filter.on_llm_request(priority=200000)
    async def dg_ctxbudget_capture(self, event: AstrMessageEvent, req):
        _cb.capture(event, req)

    @filter.on_llm_request(priority=200001)
    async def dg_ctxbudget_gate(self, event: AstrMessageEvent, req):
        await _cb.gate(event, req)

    @filter.command("上下文", alias={"ctxbudget", "占用", "上下文诊断"})
    async def dg_ctxbudget_cmd(self, event: AstrMessageEvent):
        """管理员用的上下文与缓存诊断。用法：/上下文"""
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
        raw=re.sub(r'^(?:(?:\[At:[^\]]*\]|\[CQ:at,[^\]]*\]|<@!?[^>]+>)\s*)+','',(event.message_str or '').strip()).strip().lstrip('/／#').strip()
        if arg in {'文件','下载','pdf','txt','文本','文字版'} or not arg and raw=='说明书':
            yield self._file_result(event,'txt' if arg in {'txt','文本','文字版'} else 'pdf')
            return
        if arg and arg.split()[0] in {"全部", "所有", "all", "完整", "分页", "文字"}:
            parts=arg.split()
            if len(parts)==1 and parts[0] not in {'分页','文字'}:
                yield self._file_result(event)
                return
            if len(parts)>2 or len(parts)==2 and not parts[1].isdigit():
                yield event.plain_result('用法：/功能 全部 发送PDF；/功能 分页 2 查看指定页。')
                return
            page=int(parts[1]) if len(parts)==2 else 1
            yield self._reply(event, lambda md: _render_full_page(page,md))
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
