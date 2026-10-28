"""大肥鱼功能说明书：/功能 一键列出她全部能做的事；/功能 语音 只看某一类。

为什么单独做成一个小插件：
  · 说明书横跨好几个插件（语音、翻唱、游戏、记忆、用量、群管…），
    放进任何一个都会随那个插件的改动一起被动
  · 命令直接返回文本、**不经过大模型 → 0 token**
  · 改文案只改这一个文件，不碰任何功能代码

对齐说明：中文是双宽字符、英文/斜杠是单宽，手工数空格一定会歪。
所以下面用 _line() 按**显示宽度**自动补空格，改文案不用再数空格。
"""
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

# 说明行：命令与描述从那第 COL 个"半宽"位置开始，保证全部左对齐
COL = 22


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
SECTIONS = [
    ("语音", "想让我开口", [
        ("/说 内容", "念一句指定的话（本地洛天依，失败自动降级 MiMo/Edge）"),
        ("/语音开 /语音关", "全程语音 / 换回文字"),
        ("/语音模式", "只发语音 或 文字+语音"),
        ("/语音状态", "看当前语音设置"),
        ("/语音测试", "测本地/MiMo/Edge 三档通不通"),
    ], ["说话", "tts"]),
    ("点歌翻唱", "唱歌的事", [
        ("/点歌 歌名", "搜歌并翻唱成洛天依（/唱 /sing）"),
        ("/翻唱", "发一段音频，换成洛天依音色（/cover）"),
        ("/发送方式", "翻唱结果发语音 / 文件 / 自动"),
    ], ["翻唱", "点歌", "sing", "cover"]),
    ("音乐", "找歌", [
        ("/搜索歌曲 词", "搜歌（回序号下载）"),
    ], ["听歌", "歌曲"]),
    ("游戏", "游戏的事", [
        ("/服务器状态", "在线、房间、玩家、延迟"),
        ("/报bug 内容", "记一笔给我们看"),
    ], ["雾锁", "服务器"]),
    ("记忆", "我的记性", [
        ("/lmem help", "记忆命令：搜索 / 遗忘 / 清理 / 统计 等"),
    ], ["lmem", "记性"]),
    ("用量", "花销", [
        ("/用量", "我今天烧了多少钱"),
    ], ["花费", "token"]),
    ("管理员", "管理与群务", [
        ("/运营帮助", "管理命令总览（群管 / 公告 / bug / 监控…）"),
    ], ["群管", "管理", "运营"]),
]

ALIAS = {}
for _name, _title, _items, _aliases in SECTIONS:
    ALIAS[_name] = _name
    for _a in _aliases:
        ALIAS[_a] = _name


def _render_section(idx: int) -> str:
    name, title, items, _ = SECTIONS[idx]
    lines = ["哼，【%s】这类本小姐会这些：（<ゝω・）☆" % name, ""]
    for cmd, desc in items:
        lines.append("  " + _line(cmd, desc, COL))
    lines.append("")
    lines.append("发 /功能 看全部；发 /功能 语音（或 游戏/翻唱/群管…）只看某一类。")
    return "\n".join(lines)


def _render_all() -> str:
    lines = [
        "哼，居然要本小姐自报家门？（<ゝω・）☆",
        "",
    ]
    for name, title, items, _ in SECTIONS:
        lines.append("—— %s ——" % title)
        for cmd, desc in items:
            lines.append("  " + _line(cmd, desc, COL))
        lines.append("")
    lines.append("懒得记？用大白话跟我说就行，我听得懂。")
    lines.append("只看某一类：/功能 语音 · /功能 翻唱 · /功能 游戏 · /功能 群管")
    return "\n".join(lines)


@register(
    "astrbot_plugin_dafeiyu_help",
    "ops",
    "大肥鱼功能说明书：/功能 列出全部功能，/功能 分类 只看某一类（不经过模型，0 token）",
    "1.1.0",
)
class DafeiyuHelpPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config

    def _arg(self, event):
        s = (event.message_str or "").strip()
        for c in ("功能", "帮助", "菜单", "能干啥", "会什么", "help", "说明书"):
            for pre in ("/", "／", "", "#"):
                t = pre + c
                if s.startswith(t):
                    return s[len(t):].strip().lstrip("/／").strip()
        return s

    @filter.command("功能", alias={"帮助", "菜单", "能干啥", "会什么", "help", "说明书"})
    async def help_cmd(self, event: AstrMessageEvent):
        """列出大肥鱼的全部功能。用法：/功能　或　/功能 语音"""
        arg = self._arg(event)
        if arg:
            key = ALIAS.get(arg)
            if key:
                idx = next(i for i, s in enumerate(SECTIONS) if s[0] == key)
                yield event.plain_result(_render_section(idx))
                return
            # 模糊匹配：包含关键字
            for i, (name, _t, _it, _al) in enumerate(SECTIONS):
                if arg in name or name in arg or any(arg in a for a in _al):
                    yield event.plain_result(_render_section(i))
                    return
        yield event.plain_result(_render_all())
