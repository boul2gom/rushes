"""Covers (task 8a): the sharpest, most animated key frames, and the picture with its big words.

The cover is 1080x1920 like the video. Its text stays in the band that survives both Instagram
grid crops of a 9:16 picture, 4:5 (1350 px tall) and 3:4 (1440 px tall), and inside the brand's
safe margins (R9).
"""

import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter, UnidentifiedImageError

from rushes.config import Layout
from rushes.core.intervals import overlap_ms
from rushes.fsutil import atomic_path
from rushes.meta.caption import part_label
from rushes.render.canvas import CANVAS_H, CANVAS_W, Rect, video_rect
from rushes.render.charter import BrandCharter
from rushes.render.text import fit_text
from rushes.schemas import ScenesFile

COVER_W, COVER_H = CANVAS_W, CANVAS_H
INSTAGRAM_4_5_H = COVER_W * 5 // 4  # 1350
WORDS_MAX = 4
_SCORE_WIDTH = 256  # frames are compared at the same width
_BLUR_RADIUS = 28
_WORDS_BOX_H, _LABEL_BOX_H, _GAP = 600, 160, 30
_JPEG_QUALITY = 90


def _sharpness(path: Path) -> float | None:
    """Variance of the Laplacian of the grayscale frame (None when it cannot be read)."""
    try:
        with Image.open(path) as image:
            gray = image.convert("L")
            height = max(3, round(_SCORE_WIDTH * gray.height / gray.width))
            small = gray.resize((_SCORE_WIDTH, height), Image.Resampling.LANCZOS)
    except OSError, UnidentifiedImageError, ValueError:
        return None
    g: NDArray[np.float64] = np.asarray(small, dtype=np.float64)
    laplacian = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    return float(laplacian.var())


def cover_candidates(
    keyframes: Sequence[tuple[int, Path]],
    *,
    n: int = 3,
    motion: Mapping[int, float] | None = None,
) -> list[tuple[int, float]]:
    """The `n` best (t_ms, score) among the key frames, best first.

    score = sharpness (variance of the Laplacian, divided by the best one of the set) x
    (0.5 + motion of the shot, 0 to 1; `motion` is keyed by t_ms and defaults to 0). Frames that
    cannot be read are ignored; ties keep the earliest frame."""
    measured = [(t, _sharpness(path)) for t, path in keyframes]
    readable = [(t, s) for t, s in measured if s is not None]
    best = max((s for _, s in readable), default=0.0)
    scored = [
        (
            t,
            (s / best if best > 0 else 0.0)
            * (0.5 + min(max((motion or {}).get(t, 0.0), 0.0), 1.0)),
        )
        for t, s in readable
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:n]


def segment_keyframes(scenes: ScenesFile | None, span: tuple[int, int]) -> list[tuple[int, float]]:
    """(t_ms, motion) of the key frame of every shot that overlaps `span`, clamped inside it."""
    if scenes is None:
        return []
    start, end = span
    found: list[tuple[int, float]] = []
    for shot in scenes.shots:
        if overlap_ms((shot.start_ms, shot.end_ms), span) > 0:
            found.append((min(max(shot.keyframe_ms, start), end - 1), shot.motion))
    return found


def cover_words(hook: str | None, name: str) -> str:
    """2 to 4 big words: the first four of the hook (two at least), else those of the name."""
    hook_words = (hook or "").split()
    words = hook_words if len(hook_words) >= 2 else name.split()
    return " ".join(words[:WORDS_MAX])


def safe_zone(charter: BrandCharter) -> Rect:
    """Where the text may go: inside both Instagram crops and the charter's safe margins."""
    top = max((COVER_H - INSTAGRAM_4_5_H) // 2, charter.safe.top)
    bottom = min(COVER_H - (COVER_H - INSTAGRAM_4_5_H) // 2, COVER_H - charter.safe.bottom)
    side = max(charter.safe.left, charter.safe.right)  # symmetric: the cover is centred
    return Rect(side, top, COVER_W - 2 * side, bottom - top)


def _cover_fit(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to fill width x height, then crop the centre."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left, top = (resized.width - width) // 2, (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _compose(frame: Image.Image, layout: Layout) -> Image.Image:
    rect = video_rect(layout, frame.width, frame.height)
    canvas = _cover_fit(frame, COVER_W, COVER_H)
    if layout != "vertical":
        canvas = canvas.filter(ImageFilter.GaussianBlur(_BLUR_RADIUS))
        canvas.paste(_cover_fit(frame, rect.w, rect.h), (rect.x, rect.y))
    return canvas


def render_cover(
    frame: Path,
    *,
    layout: Layout,
    words: str,
    part_number: int | None,
    charter: BrandCharter,
    language: str,
    out: Path,
) -> None:
    """1080x1920 JPEG: the frame placed like the video of `layout`, the words big in the safe
    zone, and "Part N" under them. ValueError for blank words, OSError for an unreadable frame."""
    if not words.strip():
        raise ValueError("mots de couverture vides")
    zone = safe_zone(charter)
    font, emoji = charter.font_path(), charter.emoji_font_path()
    with Image.open(frame) as opened:
        canvas = _compose(opened.convert("RGB"), layout).convert("RGBA")
    block = [
        fit_text(
            unicodedata.normalize("NFC", words),
            font=font,
            emoji_font=emoji,
            box=Rect(0, 0, zone.w, _WORDS_BOX_H),
            max_px=170,
            min_px=70,
            max_lines=3,
            fill=charter.highlight,
            stroke_px=8,
        )
    ]
    label = part_label(language, part_number)
    if label is not None:
        block.append(
            fit_text(
                label,
                font=font,
                emoji_font=emoji,
                box=Rect(0, 0, zone.w, _LABEL_BOX_H),
                max_px=100,
                min_px=50,
                max_lines=1,
                fill=charter.banner_fg,
                stroke_px=6,
                stroke_fill=charter.banner_bg,
            )
        )
    height = sum(part.height for part in block) + _GAP * (len(block) - 1)
    y = zone.y + (zone.h - height) // 2
    for part in block:
        canvas.alpha_composite(part, (zone.x + (zone.w - part.width) // 2, y))
        y += part.height + _GAP
    with atomic_path(out) as tmp:
        canvas.convert("RGB").save(tmp, format="JPEG", quality=_JPEG_QUALITY)
