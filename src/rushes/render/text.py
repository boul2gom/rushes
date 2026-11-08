"""Text to RGBA image with Pillow (no FFmpeg text filter exists on every build, C46).

`fit_text` shrinks the text first, then goes to two lines, then ends with an ellipsis. A character
missing from the cmap of the main font (read with fontTools) is drawn with the emoji font. Lines
are measured by their real ink (both fonts), so accents and emoji taller than the font's ascent
are never cut. Scripts that neither font covers (Arabic, CJK...) draw notdef boxes.
"""

import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from rushes.render.canvas import Rect

ELLIPSIS = "\u2026"
_VARIATION_SELECTORS = frozenset("\ufe0e\ufe0f")  # no glyph of their own: dropped
_ZWJ = "\u200d"  # kept in the run, so that the emoji font can join the sequence
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_REGIONAL = range(0x1F1E6, 0x1F200)
_STEP_PX = 2
_LINE_GAP = 0.12  # of the font size
_PAD = 2  # empty rows and columns around the ink: antialiasing never touches the edge
EMOJI_WEIGHT = 700  # the emoji font is variable; bold matches Luckiest Guy and Bebas Neue


@cache
def _cmap(font: Path) -> frozenset[int]:
    with TTFont(font, lazy=True) as ttf:
        return frozenset(ttf.getBestCmap() or {})


@cache
def _load(font: Path, size: int) -> ImageFont.FreeTypeFont:
    loaded = ImageFont.truetype(str(font), size)
    try:
        axes = loaded.get_variation_axes()
    except OSError:  # not a variable font
        return loaded
    if len(axes) == 1:  # the weight axis of the emoji font
        low, high = float(axes[0]["minimum"] or 0), float(axes[0]["maximum"] or EMOJI_WEIGHT)
        loaded.set_variation_by_axes([min(max(float(EMOJI_WEIGHT), low), high)])
    return loaded


@dataclass(frozen=True)
class _Fonts:
    main: Path
    emoji: Path

    def pick(self, char: str) -> Path:
        """Main font when it has the glyph, else the emoji font, else the main one (its notdef)."""
        if ord(char) in _cmap(self.main) or char.isspace():
            return self.main
        return self.emoji if ord(char) in _cmap(self.emoji) else self.main


def _runs(text: str, fonts: _Fonts) -> list[tuple[Path, str]]:
    runs: list[tuple[Path, str]] = []
    for char in text:
        if char in _VARIATION_SELECTORS:
            continue
        font = runs[-1][0] if char == _ZWJ and runs else fonts.pick(char)
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + char)
        else:
            runs.append((font, char))
    return runs


def _clusters(text: str) -> list[str]:
    """Characters grouped so that no cut splits a visible symbol: a base with its combining marks,
    variation selectors, skin tone, joined (ZWJ) emoji and flags (pairs of regional indicators)."""
    out: list[str] = []
    for char in text:
        code = ord(char)
        glue = bool(out) and (
            unicodedata.combining(char) != 0
            or char in _VARIATION_SELECTORS
            or char == _ZWJ
            or code in _SKIN_TONES
            or out[-1].endswith(_ZWJ)
            or (code in _REGIONAL and len(out[-1]) == 1 and ord(out[-1]) in _REGIONAL)
        )
        if glue:
            out[-1] += char
        else:
            out.append(char)
    return out


@cache
def _width(text: str, fonts: _Fonts, size: int) -> float:
    return sum(_load(font, size).getlength(run) for font, run in _runs(text, fonts))


@cache
def _ink(text: str, fonts: _Fonts, size: int) -> tuple[int, int]:
    """(top, bottom) of the ink of `text` around its baseline (top is negative above it)."""
    top, bottom = 0, 0
    for font, run in _runs(text, fonts):
        _, run_top, _, run_bottom = _load(font, size).getbbox(run, anchor="ls")
        top, bottom = min(top, math.floor(run_top)), max(bottom, math.ceil(run_bottom))
    return top, bottom


def _block(lines: list[str], fonts: _Fonts, size: int) -> tuple[int, int, int]:
    """(top, bottom, row pitch) shared by every line: the largest ink above and under a baseline."""
    top = min((_ink(line, fonts, size)[0] for line in lines), default=0)
    bottom = max((_ink(line, fonts, size)[1] for line in lines), default=0)
    return top, bottom, bottom - top + math.ceil(size * _LINE_GAP)


def _height(lines: list[str], fonts: _Fonts, size: int) -> int:
    top, bottom, pitch = _block(lines, fonts, size)
    return (len(lines) - 1) * pitch + bottom - top + 2 * _PAD


def _wrap(text: str, fonts: _Fonts, size: int, width: int) -> list[str]:
    """Greedy word wrap; a word wider than a line is cut between symbols."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if _width(candidate, fonts, size) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for cluster in _clusters(word):
            if current and _width(current + cluster, fonts, size) > width:
                lines.append(current)
                current = ""
            current += cluster
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class TextLayout:
    size: int
    lines: list[str]
    truncated: bool  # the text was cut and ends with an ellipsis


@dataclass(frozen=True)
class _Space:
    fonts: _Fonts
    width: int  # inside the outline and the padding
    height: int
    max_lines: int

    def fits(self, lines: list[str], size: int, max_lines: int) -> bool:
        if not lines or len(lines) > max_lines:
            return False
        if _height(lines, self.fonts, size) > self.height:
            return False
        return all(_width(line, self.fonts, size) <= self.width for line in lines)


def _largest_fitting(count: int, fits: Callable[[int], bool]) -> int:
    """Largest k in 1..count with fits(k), by binary search (a longer cut never fits better);
    0 when even 1 does not."""
    low, high = 0, count
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _truncate(text: str, space: _Space, size: int) -> list[str]:
    """Longest start of `text` that fits with an ellipsis: whole words first, then symbols."""
    words = text.split()

    def fit_words(k: int) -> bool:
        lines = _wrap(" ".join(words[:k]) + ELLIPSIS, space.fonts, size, space.width)
        return space.fits(lines, size, space.max_lines)

    kept = _largest_fitting(len(words), fit_words)
    if kept:
        return _wrap(" ".join(words[:kept]) + ELLIPSIS, space.fonts, size, space.width)
    parts = _clusters(" ".join(words))

    def fit_clusters(k: int) -> bool:
        lines = _wrap("".join(parts[:k]).rstrip() + ELLIPSIS, space.fonts, size, space.width)
        return space.fits(lines, size, space.max_lines)

    kept = _largest_fitting(len(parts), fit_clusters)
    return _wrap("".join(parts[:kept]).rstrip() + ELLIPSIS, space.fonts, size, space.width)


def plan_text(
    text: str,
    *,
    font: Path,
    emoji_font: Path,
    box: Rect,
    max_px: int,
    min_px: int,
    max_lines: int = 2,
    stroke_px: int = 0,
) -> TextLayout:
    """Size and lines of `text` in `box`: shrink down to `min_px` on one line, then allow
    `max_lines` lines (from `max_px` again), then cut with an ellipsis at `min_px`.

    ValueError when the box cannot hold one line of `min_px` (or the text is blank)."""
    if min_px > max_px or min_px <= 0:
        raise ValueError(f"tailles invalides : {min_px}-{max_px}")
    flat = " ".join(unicodedata.normalize("NFC", text).split())
    if not flat:
        raise ValueError("texte vide")
    fonts = _Fonts(font, emoji_font)
    # _height already counts the padding, not the outline
    space = _Space(fonts, box.w - 2 * (stroke_px + _PAD), box.h - 2 * stroke_px, max_lines)
    if (
        space.width < _width(ELLIPSIS, fonts, min_px)
        or _height([flat], fonts, min_px) > space.height  # one line of this text
    ):
        raise ValueError(f"zone {box.w}x{box.h} trop petite pour une ligne de {min_px} px")
    for line_count in range(1, max_lines + 1):  # shrink first, then one more line
        sizes = [*range(max_px, min_px, -_STEP_PX), min_px]
        for size in sizes:
            lines = _wrap(flat, fonts, size, space.width)
            if space.fits(lines, size, line_count):
                return TextLayout(size, lines, truncated=False)
    return TextLayout(min_px, _truncate(flat, space, min_px), truncated=True)


def fit_text(
    text: str,
    *,
    font: Path,
    emoji_font: Path,
    box: Rect,
    max_px: int,
    min_px: int,
    max_lines: int = 2,
    fill: str = "#FFFFFF",
    stroke_px: int = 0,
    stroke_fill: str = "#000000",
) -> Image.Image:
    """RGBA image of `text`, centred line by line, never larger than `box`.

    The image is the size of the text (its outline and a 2 px margin included), not of the box.
    The text is normalised to NFC first. See `plan_text` for the fitting order."""
    layout = plan_text(
        text,
        font=font,
        emoji_font=emoji_font,
        box=box,
        max_px=max_px,
        min_px=min_px,
        max_lines=max_lines,
        stroke_px=stroke_px,
    )
    fonts, size, lines = _Fonts(font, emoji_font), layout.size, layout.lines
    top, bottom, pitch = _block(lines, fonts, size)
    widths = [_width(line, fonts, size) for line in lines]
    margin = stroke_px + _PAD
    image = Image.new(
        "RGBA",
        (
            math.ceil(max(widths)) + 2 * margin,
            (len(lines) - 1) * pitch + bottom - top + 2 * margin,
        ),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    for row, (line, line_w) in enumerate(zip(lines, widths, strict=True)):
        x = (image.width - line_w) / 2
        baseline = margin - top + row * pitch
        for run_font, run in _runs(line, fonts):
            draw.text(
                (x, baseline),
                run,
                font=_load(run_font, size),
                fill=fill,
                anchor="ls",
                stroke_width=stroke_px,
                stroke_fill=stroke_fill,
            )
            x += _load(run_font, size).getlength(run)
    return image
