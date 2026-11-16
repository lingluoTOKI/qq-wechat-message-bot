"""Pillow-based fallback renderer.

When wkhtmltopdf isn't installed (Debian 13 dropped the package, Docker
containers without xvfb, etc.) we still want image output. Pillow is a
much smaller dep and ships with most Python installs, so we use it to
render a simple card-style image — visually less rich than the HTML
version, but still readable and unifrom.

Notable simplifications vs. the HTML renderer:
  * No background blur, gradients, or radial glows
  * Uses the best font we can discover. A system CJK font is preferred;
    otherwise a bundled offline Noto Sans SC subset is used, and a missing
    CJK font never prevents image output.
  * No code blocks / tables (rendered as plain monospaced lines)
  * Uses solid color cards with a left accent strip
"""

from __future__ import annotations

import functools
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.exceptions import PartialRenderError, RenderError
from ..core.logging import get_logger
from .pagination import split_by_chapters
from .theme import card_color_for

if TYPE_CHECKING:  # pragma: no cover
    from PIL.ImageFont import FreeTypeFont

logger = get_logger("BiliVideo/PillowRender")


# ──────────────────────── font discovery ────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Offline CJK font bundled with the plugin so the Pillow fallback produces
# real Chinese text on a bare container (e.g. Zeabur/Docker) with no system
# CJK font and no outbound network. Subset of Noto Sans SC (GB2312 + Latin +
# punctuation), SIL OFL 1.1 — see fonts/NotoSansSC-LICENSE.txt. System CJK
# fonts are preferred for fuller coverage, so the bundle is tried last.
_BUNDLED_CJK_FONT = str(_REPO_ROOT / "fonts" / "NotoSansSC-Regular.subset.otf")


_CJK_FONT_CANDIDATES: tuple[str, ...] = (
    # Linux
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/simsun.ttc",
    # Bundled offline last-resort CJK font (zero system deps, no network).
    _BUNDLED_CJK_FONT,
)
_FALLBACK_FONT_CANDIDATES: tuple[str, ...] = (
    str(_REPO_ROOT / "fonts" / "JetBrainsMono-Light.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/mnt/c/Windows/Fonts/arial.ttf",
)


@functools.lru_cache(maxsize=1)
def _find_cjk_font() -> str | None:
    for candidate in _CJK_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def _find_fallback_font() -> str | None:
    for candidate in _FALLBACK_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def check_pillow_ready() -> tuple[bool, str]:
    """Return whether Pillow can produce images in this environment.

    Never blocks image output: a CJK font (system, else the bundled offline
    subset) is preferred; failing that we fall back to a Latin font and
    finally Pillow's built-in default, so rendering always proceeds.
    """

    try:
        from PIL import ImageFont
    except ImportError as exc:
        return False, f"Pillow not installed: {exc}"

    font_path = _find_cjk_font()
    if font_path is not None:
        try:
            ImageFont.truetype(font_path, 14)
        except Exception as exc:
            return False, f"CJK font cannot be loaded: {font_path} ({exc})"
        return True, f"font={font_path}"

    fallback_path = _find_fallback_font()
    if fallback_path is None:
        return True, "ready with Pillow default font; no CJK font discovered"
    try:
        ImageFont.truetype(fallback_path, 14)
    except Exception as exc:
        return False, f"fallback font cannot be loaded: {fallback_path} ({exc})"
    return True, f"fallback_font={fallback_path}; no CJK font discovered"


# FreeTypeFont objects are not safe to share across threads, so loaded fonts
# are cached per-thread keyed by (path, size).
_FONT_CACHE = threading.local()


def _load_font(size: int):
    from PIL import ImageFont

    font_path = _find_cjk_font() or _find_fallback_font()
    if font_path is not None:
        cache: dict = getattr(_FONT_CACHE, "fonts", None)
        if cache is None:
            cache = {}
            _FONT_CACHE.fonts = cache
        cached = cache.get((font_path, size))
        if cached is not None:
            return cached, font_path
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception as exc:
            logger.warning(f"font load failed ({font_path}): {exc}; using Pillow default")
        else:
            cache[(font_path, size)] = font
            return font, font_path
    return ImageFont.load_default(), "Pillow default"


# Codepoints the bundled GB2312 Noto subset (and most single CJK fonts Pillow
# loads) cannot draw, so they would appear as .notdef "tofu" boxes. Pillow does
# no cross-font glyph fallback, so we drop them up front in the Pillow path; the
# richer wkhtml/HTML path keeps them.
_UNSUPPORTED_GLYPHS_RE = re.compile(
    "["
    "\U0001f000-\U0001ffff"  # emoji + supplemental symbols & pictographs
    "\U00002600-\U000027bf"  # miscellaneous symbols + dingbats (✅✨🔥…)
    "\U00002b00-\U00002bff"  # misc symbols & arrows (★ ⬆ …)
    "\U00002300-\U000023ff"  # misc technical (⏱ ⌚ ⏰ …)
    "\U0000fe00-\U0000fe0f"  # emoji variation selectors
    "‍"  # zero-width joiner
    "]"
)


def _strip_unsupported_glyphs(text: str) -> str:
    """Remove emoji / symbol codepoints a single CJK font can't render."""

    if not text:
        return text
    cleaned = _UNSUPPORTED_GLYPHS_RE.sub("", text)
    if cleaned == text:
        return text
    # collapse the spaces left where a stripped emoji used to sit
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


# Leading timestamp token. After glyph stripping the ⏱ marker is gone but the
# digits remain: "12:34 …", "[12:34] …", "1:02:03 …". Highlighted in cards.
_TS_RE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*")

# Trailing timestamp on a chapter heading: "## 核心机制 01:03" / "01:03*".
_TS_TAIL_RE = re.compile(r"[\s*·—\-]*(\d{1,2}:\d{2}(?::\d{2})?)\s*[*。·]?\s*$")


# ──────────────────────── inline markdown ────────────────────────

# Matches the emphasis / code spans the model may still emit even though the
# prompt forbids them. Splitting on this lets us drop the punctuation while
# knowing which spans were meant to be emphasised.
_INLINE_SPLIT_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|\*[^*\n]+?\*|`[^`\n]+?`)")


def _inline_runs(text: str) -> list[tuple[str, bool]]:
    """Split a line into `(text, bold)` runs, stripping markdown punctuation."""

    runs: list[tuple[str, bool]] = []
    for part in _INLINE_SPLIT_RE.split(text):
        if not part:
            continue
        if len(part) > 4 and part.startswith("**") and part.endswith("**"):
            runs.append((part[2:-2], True))
        elif len(part) > 4 and part.startswith("__") and part.endswith("__"):
            runs.append((part[2:-2], True))
        elif len(part) > 2 and part[0] == "*" and part[-1] == "*":
            runs.append((part[1:-1], False))
        elif len(part) > 2 and part[0] == "`" and part[-1] == "`":
            runs.append((part[1:-1], False))
        else:
            runs.append((part, False))
    if not runs:
        return [("", False)]
    # merge neighbours with the same style so drawing is a single call
    merged: list[tuple[str, bool]] = []
    for chunk, bold in runs:
        if merged and merged[-1][1] == bold:
            merged[-1] = (merged[-1][0] + chunk, bold)
        else:
            merged.append((chunk, bold))
    return merged


def _plain(runs: Sequence[tuple[str, bool]]) -> str:
    return "".join(chunk for chunk, _ in runs)


def _clean_title(text: str) -> str:
    """Drop the placeholder author suffix the model used to append."""

    cleaned = text.strip()
    # "# 标题 - 作者名", "# 标题 - 作者名(续)", "# 标题 - UP主"
    cleaned = re.sub(r"\s*[-–—]\s*(作者名|UP主|up主|作者)(\s*[(（]续[)）])?\s*$", "", cleaned)
    cleaned = re.sub(r"\s*[(（]?\d+\s*/\s*\d+\s*[)）]?\s*$", "", cleaned)  # trailing (1/2)
    cleaned = re.sub(r"\s*[(（]续[)）]\s*$", "", cleaned)
    return cleaned.strip() or "AI 视频总结"


def _line_advance(font: object) -> int:
    """Vertical advance per line — generous leading for CJK legibility."""

    size = int(getattr(font, "size", 16))
    return max(size + 8, round(size * 1.62))


# ──────────────────────── markdown parsing ────────────────────────


@dataclass(slots=True)
class _Block:
    kind: str  # "h1" | "h2" | "h3" | "p" | "li"
    text: str
    runs: list[tuple[str, bool]] = field(default_factory=list)

    def styled(self) -> list[tuple[str, bool]]:
        return self.runs or [(self.text, False)]


def _parse_markdown_blocks(markdown_text: str) -> list[_Block]:
    """Very small markdown-ish tokenizer producing block-level elements."""

    out: list[_Block] = []
    in_code = False
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(_Block("p", line, _inline_runs(line)))
            continue
        if not line.strip():
            continue
        if line.startswith("# "):
            out.append(_Block("h1", line[2:].strip()))
        elif line.startswith("## "):
            out.append(_Block("h2", line[3:].strip()))
        elif line.startswith("### "):
            out.append(_Block("h3", line[4:].strip()))
        elif line.startswith(("- ", "* ", "+ ")):
            # 有些模型会把 "1. 内容" 写在项目符号后面, 去掉残留编号
            body = re.sub(r"^\d+\s*[.、)）]\s*", "", line[2:].strip())
            runs = _inline_runs(body)
            out.append(_Block("li", _plain(runs), runs))
        elif re.match(r"^\d+\.\s", line):
            runs = _inline_runs(re.sub(r"^\d+\.\s", "", line).strip())
            out.append(_Block("li", _plain(runs), runs))
        elif line.startswith("> "):
            runs = _inline_runs("“" + line[2:].strip() + "”")
            out.append(_Block("p", _plain(runs), runs))
        else:
            runs = _inline_runs(line.strip())
            out.append(_Block("p", _plain(runs), runs))
    stripped: list[_Block] = []
    for block in out:
        text = _strip_unsupported_glyphs(block.text)
        if text:
            runs = [(chunk, bold) for chunk, bold in block.styled()]
            runs = [(t, b) for t, b in ((_strip_unsupported_glyphs(c), d) for c, d in runs) if t]
            if not runs:
                continue
            stripped.append(_Block(block.kind, _plain(runs), runs))
    return stripped


# ──────────────────────── renderer ────────────────────────


class PillowRenderer:
    """Render Markdown into PNG cards using only Pillow.

    Layout: dark background, single column, one card per `## chapter`
    section, with a left accent stripe color-cycled through the same
    palette as the wkhtmltopdf renderer.
    """

    BG = (24, 26, 40)
    CARD_BG = (33, 37, 55)
    TITLE_FG = (240, 244, 250)
    TEXT_FG = (184, 192, 208)
    BOLD_FG = (238, 243, 250)
    ACCENT_FG = (109, 176, 226)
    TS_FG = (126, 148, 170)
    DIM_FG = (104, 114, 130)
    TITLE_SIZE = 24
    H2_SIZE = 18
    H3_SIZE = 16
    BODY_SIZE = 17
    PADDING = 36
    CARD_PADDING = 28
    CARD_GAP = 20
    BLOCK_GAP = 8
    STRIPE_W = 4
    BULLET_INDENT = 20
    SUBSECTION_INDENT = 14
    FOOTER_H = 46
    CARD_RADIUS = 12

    def __init__(self, *, output_dir: str | Path, image_width: int = 900) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._width = image_width

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def render(
        self,
        markdown_text: str,
        *,
        base_filename: str,
        max_cards_per_image: int = 6,
        enable_split: bool = True,
    ) -> list[Path]:
        chapter_count = sum(1 for line in markdown_text.splitlines() if line.startswith("## "))
        if not enable_split or chapter_count <= max_cards_per_image:
            return self._render_one(markdown_text, base_filename, page_label=None, total=1)

        pages = split_by_chapters(markdown_text, max_cards=max_cards_per_image)
        outputs: list[Path] = []
        failed_pages: list[int] = []
        page_errors: dict[int, str] = {}
        total = len(pages)
        for idx, page in enumerate(pages, start=1):
            label = None if total == 1 else f"({idx}/{total})"
            try:
                outputs.extend(
                    self._render_one(page, f"{base_filename}_p{idx}", page_label=label, total=total)
                )
            except RenderError as exc:
                logger.warning(
                    f"page {idx}/{total} pillow render failed: {exc}; "
                    f"page_chars={len(page)} chapters={page.count(chr(10) + '## ')}"
                )
                failed_pages.append(idx)
                page_errors[idx] = str(exc)
        if failed_pages and outputs:
            raise PartialRenderError(
                f"partial pillow render failed; failed_pages={failed_pages}, "
                f"succeeded_pages={[p.name for p in outputs]}",
                generated_paths=outputs,
                failed_pages=failed_pages,
                page_errors=page_errors,
            )
        if not outputs:
            raise RenderError("all pages failed to render with Pillow")
        return outputs

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _render_one(
        self,
        markdown_text: str,
        base_filename: str,
        *,
        page_label: str | None,
        total: int,
    ) -> list[Path]:
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RenderError(f"Pillow not installed: {exc}") from exc

        blocks = _parse_markdown_blocks(markdown_text)
        if not blocks:
            blocks.append(_Block("p", "(空内容)"))

        title_block = next((b for b in blocks if b.kind == "h1"), None)
        body_blocks = [b for b in blocks if b is not title_block]
        title_text = _clean_title(title_block.text if title_block else "AI 视频总结")

        f_title, font_path = _load_font(self.TITLE_SIZE)
        f_h2, _ = _load_font(self.H2_SIZE)
        f_h3, _ = _load_font(self.H3_SIZE)
        f_body, _ = _load_font(self.BODY_SIZE)

        content_w = self._width - self.PADDING * 2
        card_inner_w = content_w - self.CARD_PADDING * 2
        title_adv = _line_advance(f_title)
        head_adv = _line_advance(f_h2)
        h3_adv = _line_advance(f_h3)
        body_adv = _line_advance(f_body)

        # Header: keep the title to at most two lines so it never eats the page.
        title_lines = self._wrap(
            title_text, font=f_title, max_width=content_w, max_lines=2, ellipsis=True
        )
        header_h = self.PADDING + len(title_lines) * title_adv + 26

        # Build the full layout once; every line is wrapped with the font it is
        # painted in, so the measured height and the painted content stay in
        # lock-step (no vertical clipping) and nothing overruns a card edge.
        layout: list[dict] = []
        chapter_no = 0
        for idx, (heading, blocks_) in enumerate(self._group_into_cards(body_blocks)):
            if heading is not None:
                chapter_no += 1
                badge = f"{chapter_no:02d}"
                head_ts = ""
                match = _TS_TAIL_RE.search(heading)
                if match:
                    head_ts = match.group(1)
                    heading = heading[: match.start()].rstrip(" \t*·—-")
                badge_w = self._text_w(f_h2, badge + "  ")
                head_lines = self._wrap(
                    heading, font=f_h2, max_width=max(1, card_inner_w - badge_w)
                )
            else:
                badge, badge_w, head_lines, head_ts = "", 0, [], ""

            items: list[dict] = []
            under_h3 = False
            for b in blocks_:
                if b.kind == "h3":
                    under_h3 = True
                    items.append(
                        {
                            "kind": "h3",
                            "lines": self._wrap_rich(b.styled(), f_h3, card_inner_w),
                            "indent": 0,
                            "adv": h3_adv,
                            "font": f_h3,
                            "color": self.ACCENT_FG,
                        }
                    )
                    continue
                indent = self.SUBSECTION_INDENT if under_h3 else 0
                if b.kind == "li":
                    avail = max(1, card_inner_w - indent - self.BULLET_INDENT)
                    items.append(
                        {
                            "kind": "li",
                            "lines": self._wrap_rich(b.styled(), f_body, avail),
                            "indent": indent,
                            "adv": body_adv,
                            "font": f_body,
                            "color": self.TEXT_FG,
                        }
                    )
                else:
                    items.append(
                        {
                            "kind": "p",
                            "lines": self._wrap_rich(
                                b.styled(), f_body, max(1, card_inner_w - indent)
                            ),
                            "indent": indent,
                            "adv": body_adv,
                            "font": f_body,
                            "color": self.TEXT_FG,
                        }
                    )

            card_h = self.CARD_PADDING * 2
            if head_lines:
                card_h += len(head_lines) * head_adv + 10
            for it in items:
                card_h += len(it["lines"]) * it["adv"] + self.BLOCK_GAP
            layout.append(
                {
                    "idx": idx,
                    "head_lines": head_lines,
                    "head_ts": head_ts,
                    "badge": badge,
                    "badge_w": badge_w,
                    "items": items,
                    "card_h": card_h,
                }
            )

        total_h = header_h + sum(c["card_h"] + self.CARD_GAP for c in layout) + self.FOOTER_H
        logger.debug(
            f"pillow page layout: chars={len(markdown_text)} cards={len(layout)} "
            f"height={total_h} width={self._width} font={font_path}"
        )

        img = Image.new("RGB", (self._width, total_h), self.BG)
        draw = ImageDraw.Draw(img)

        # Header (multi-line title + width-matched accent underline)
        ty = self.PADDING
        for line in title_lines:
            draw.text((self.PADDING, ty), line, fill=self.TITLE_FG, font=f_title)
            ty += title_adv
        underline_w = (
            min(content_w, max(120, self._text_w(f_title, title_lines[0])))
            if title_lines
            else 120
        )
        draw.line((self.PADDING, ty + 4, self.PADDING + underline_w, ty + 4),
                  fill=self.ACCENT_FG, width=4)

        y = header_h
        for card in layout:
            # 单一主色: 不再按章节轮换六种高饱和颜色, 整页只有一种强调色
            border_rgb = self.ACCENT_FG
            card_h = card["card_h"]
            card_x0 = self.PADDING
            card_x1 = self._width - self.PADDING
            draw.rounded_rectangle(
                (card_x0, y, card_x1, y + card_h), radius=self.CARD_RADIUS, fill=self.CARD_BG
            )
            draw.rounded_rectangle(
                (card_x0, y, card_x0 + self.STRIPE_W * 3, y + card_h),
                radius=self.CARD_RADIUS,
                fill=border_rgb,
            )
            draw.rectangle(
                (card_x0 + self.STRIPE_W * 2, y, card_x0 + self.STRIPE_W * 3, y + card_h),
                fill=border_rgb,
            )

            cx = card_x0 + self.CARD_PADDING
            cy = y + self.CARD_PADDING
            head_lines = card["head_lines"]
            if head_lines:
                draw.text((cx, cy), card["badge"], fill=border_rgb, font=f_h2)
                for i, hl in enumerate(head_lines):
                    hx = cx + card["badge_w"] if i == 0 else cx
                    draw.text((hx, cy), hl, fill=self.TITLE_FG, font=f_h2)
                    if i == 0 and card["head_ts"]:
                        # 时间戳跟在小节标题右侧, 用弱色, 不再带任何符号
                        hx += self._text_w(f_h2, hl) + 10
                        draw.text((hx, cy), card["head_ts"], fill=self.TS_FG, font=f_h2)
                    cy += head_adv
                cy += 12

            for it in card["items"]:
                lx = cx + it["indent"]
                font, color, adv = it["font"], it["color"], it["adv"]
                if it["kind"] == "li":
                    draw.ellipse((lx, cy + adv // 2 - 3, lx + 6, cy + adv // 2 + 3), fill=self.ACCENT_FG)
                    tx = lx + self.BULLET_INDENT
                    for line in it["lines"]:
                        self._draw_runs(draw, tx, cy, line, font=font, color=color)
                        cy += adv
                else:
                    for line in it["lines"]:
                        self._draw_runs(draw, lx, cy, line, font=font, color=color)
                        cy += adv
                cy += self.BLOCK_GAP

            y += card_h + self.CARD_GAP

        footer_y = total_h - 30
        footer_text = (
            f"Powered by biliVideo · AI 视频总结 · {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        draw.text((self.PADDING, footer_y), footer_text, fill=self.DIM_FG, font=f_body)
        if total > 1 and page_label:
            # 页码放页脚右侧, 不再挂在标题上
            label = page_label.strip("() ")
            label_w = self._text_w(f_body, label)
            draw.text(
                (self._width - self.PADDING - label_w, footer_y),
                label,
                fill=self.DIM_FG,
                font=f_body,
            )

        out = self._output_dir / f"{base_filename}.png"
        try:
            img.save(out, "PNG", optimize=True)
        except OSError as exc:
            raise RenderError(f"Pillow save failed: {exc}") from exc
        logger.info(f"pillow rendered {out.name} ({out.stat().st_size} bytes)")
        return [out]

    @staticmethod
    def _group_into_cards(
        blocks: Sequence[_Block],
    ) -> list[tuple[str | None, list[_Block]]]:
        cards: list[tuple[str | None, list[_Block]]] = []
        current: list[_Block] = []
        current_heading: str | None = None
        for b in blocks:
            if b.kind == "h2":
                if current_heading is not None or current:
                    cards.append((current_heading, current))
                current = []
                current_heading = b.text
            else:
                current.append(b)
        cards.append((current_heading, current))
        return cards

    @staticmethod
    def _wrap(
        text: str,
        *,
        font: FreeTypeFont,
        max_width: int,
        max_lines: int | None = None,
        ellipsis: bool = False,
    ) -> list[str]:
        """Break a string into wrapped lines without splitting CJK characters."""

        if not text:
            return [""]

        # Accumulate per-character advance widths instead of re-measuring the
        # whole accumulated string each iteration (which is O(n²) and dominates
        # render time for long summaries).
        lines: list[str] = []
        current = ""
        current_w = 0.0
        for ch in text:
            try:
                ch_w = font.getlength(ch)
            except Exception:
                ch_w = font.size // 2
            if current and current_w + ch_w > max_width:
                lines.append(current)
                current = ch
                current_w = ch_w
            else:
                current += ch
                current_w += ch_w
        if current:
            lines.append(current)
        lines = lines or [""]
        if max_lines is not None and len(lines) > max_lines:
            kept = lines[:max_lines]
            if ellipsis:
                kept[-1] = kept[-1].rstrip("…") + "…"
            return kept
        return lines

    @classmethod
    def _wrap_rich(
        cls,
        runs: Sequence[tuple[str, bool]],
        font: FreeTypeFont,
        max_width: int,
    ) -> list[list[tuple[str, bool]]]:
        """Wrap styled runs into lines, keeping the per-character style."""

        lines: list[list[tuple[str, bool]]] = []
        current: list[tuple[str, bool]] = []
        current_w = 0.0
        for chunk, bold in runs:
            for ch in chunk:
                try:
                    ch_w = font.getlength(ch)
                except Exception:
                    ch_w = font.size // 2
                if current and current_w + ch_w > max_width:
                    lines.append(current)
                    current = []
                    current_w = 0.0
                if current and current[-1][1] == bold:
                    current[-1] = (current[-1][0] + ch, bold)
                else:
                    current.append((ch, bold))
                current_w += ch_w
        if current:
            lines.append(current)
        return lines or [[("", False)]]

    @classmethod
    def _draw_runs(
        cls,
        draw: object,
        x: int,
        y: int,
        runs: Sequence[tuple[str, bool]],
        *,
        font: object,
        color: tuple[int, int, int],
    ) -> None:
        """Draw a styled line run by run, advancing x with each measured width."""

        cx = x
        for chunk, bold in runs:
            if not chunk:
                continue
            draw.text((cx, y), chunk, fill=cls.BOLD_FG if bold else color, font=font)
            try:
                advance = draw.textlength(chunk, font=font)
            except Exception:
                advance = cls._text_w(font, chunk)
            cx += advance

    @staticmethod
    def _text_w(font: FreeTypeFont, text: str) -> int:
        """Pixel width of `text` in `font`, robust to font backend quirks."""

        try:
            return int(font.getlength(text))
        except Exception:
            try:
                bbox = font.getbbox(text)
                return bbox[2] - bbox[0]
            except Exception:
                return len(text) * (int(getattr(font, "size", 16)) // 2)

    @classmethod
    def _draw_line(
        cls, draw: object, x: int, y: int, text: str, *, font: object, color, first: bool
    ) -> None:
        """Draw one wrapped line, accent-coloring a leading timestamp token."""

        if first and text:
            match = _TS_RE.match(text)
            if match:
                token = text[: match.end()]
                draw.text((x, y), token, fill=cls.TS_FG, font=font)
                try:
                    advance = draw.textlength(token, font=font)
                except Exception:
                    advance = cls._text_w(font, token)
                draw.text((x + advance, y), text[match.end() :], fill=color, font=font)
                return
        draw.text((x, y), text, fill=color, font=font)

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
        s = hex_str.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in s):
            return PillowRenderer.ACCENT_FG
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
