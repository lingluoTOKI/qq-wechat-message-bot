"""Prompt templates for note generation.

Centralizing the prompt makes it trivial to A/B different copy and to share
the core requirements between the three styles. The original prompt's
intent is preserved verbatim where it mattered.
"""

from __future__ import annotations

from ..core.types import TranscriptSegment

BASE_PROMPT = """\
你是一个专业的总结助手,擅长将视频转录内容整理成清晰、有条理且信息丰富的总结。

语言要求:
- 总结必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。

视频标题:
{video_title}

视频标签:
{tags}

输出说明:
- 仅返回最终的 Markdown 内容。
- 不要将输出包裹在代码块中(例如 ```markdown 或 ```)。

格式要求(非常重要,逐条照做):
- 第一行必须是 h1 标题,格式为 `# 视频标题`,只写标题本身。
- 严禁写"作者名""UP主""张三"这类占位文字;不知道作者就只写标题。
- 用 `## 章节标题` 分隔内容,章节数量 5~8 个,每个章节标题不超过 20 个字。
- 整篇总结只能有第一行那一个 h1,不要出现第二个 `# `。
- 每个章节写 5~8 条以 `- ` 开头的要点,每条不超过 40 个字,一条讲清一个点,该带数字就带数字。
- 严禁使用 `**加粗**`、`*斜体*`、`__下划线__`、行内反引号等任何强调符号;要突出就靠措辞。
- 严禁使用 `1.` `2.` `3.` 这类有序列表,只用 `- ` 无序列表,也不要嵌套子列表。
- 不要以"本视频""作者表示""up主介绍"这类套话开头,直接给结论。
- 专有名词(分队名、盟约名、干员名)保持原样,不要翻译、不要加书名号。

视频分段(格式:开始时间 - 内容):

---
{segment_text}
---

你的任务:
根据上面的分段转录内容,生成结构化的总结,遵循以下原则:

1. **完整信息**:记录尽可能多的相关细节。
2. **去除无关内容**:省略广告、填充词、问候语和不相关的言论。
3. **保留关键细节**:保留重要事实、示例、结论和建议。
4. **可读布局**:必要时使用项目符号,并保持段落简短。
5. 视频中提及的数学公式必须保留,并以 LaTeX 语法形式呈现。

额外重要的任务如下(每一个都必须严格完成):
"""

LINK_INSTRUCTION = (
    "9. 原片跳转: 在每个章节标题末尾追加时间戳,格式为 `## 章节标题 mm:ss`;"
    "时间戳只保留数字和冒号,不要加方括号、星号、反引号或其他符号。"
)

AI_SUMMARY_INSTRUCTION = (
    "10. 结尾总结: 在最后加一个 `## 总结` 章节,用 3 句话以内、总共不超过 80 字概括全片;"
    "标题只写 `总结`,不要写 `AI 总结`、不要加任何符号。"
)

NOTE_STYLES: dict[str, str] = {
    "concise": (
        "简洁模式: 只提取核心观点和关键结论,每个章节 3~4 条要点。"
        "省略细节和举例,每条控制在 30 字以内,整体 6~8 个要点。"
    ),
    "detailed": (
        "详细模式: 完整记录视频内容,每个章节 6~7 条要点,保留关键例子、数据和论证过程;"
        "每条可写满 40 字,但不要为凑字数而重复。"
    ),
    "professional": (
        "专业模式: 结构化分析,章节按「概述 / 核心机制 / 具体做法 / 实战 / 结论」这类顺序组织;"
        "每个章节 5~8 条要点,每条 40 字以内,语言干脆,不写套话,能带数据就带数据。"
    ),
}


def format_time(seconds: float) -> str:
    """Format seconds as `mm:ss` or `h:mm:ss`."""

    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render_segment_text(segments: tuple[TranscriptSegment, ...]) -> str:
    return "\n".join(f"{format_time(seg.start)} - {seg.text.strip()}" for seg in segments)


def build_prompt(
    *,
    title: str,
    segments: tuple[TranscriptSegment, ...],
    tags: str = "",
    style: str | None = None,
    enable_link: bool = False,
    enable_summary: bool = True,
) -> str:
    """Compose the final prompt sent to the LLM."""

    body = BASE_PROMPT.format(
        video_title=title,
        segment_text=render_segment_text(segments),
        tags=tags,
    )
    pieces = [body]
    if enable_link:
        pieces.append(LINK_INSTRUCTION)
    if enable_summary:
        pieces.append(AI_SUMMARY_INSTRUCTION)
    if style and style in NOTE_STYLES:
        pieces.append(NOTE_STYLES[style])
    return "\n".join(pieces)
