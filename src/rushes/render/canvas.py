"""The 9:16 canvas and where the picture goes on it (spec section 12, R8).

The video zone starts 420 px from the top (the banner is above, task 7b) and `full` keeps 480 px
free below it. `plate_filter` builds the whole video graph of the plate: a blurred copy of the
picture as background (none for `vertical`), the foreground cropped or scaled onto `video_rect`.
All sizes are the DISPLAYED ones (pixel aspect ratio applied).
"""

import re
from dataclasses import dataclass

from rushes.config import Layout
from rushes.media.verify import HEIGHT, WIDTH

CANVAS_W, CANVAS_H, VIDEO_TOP = WIDTH, HEIGHT, 420
BOTTOM_RESERVE = 480  # kept free under a `full` picture (progress bar, end card)
FULL_MAX_H = CANVAS_H - VIDEO_TOP - BOTTOM_RESERVE  # 1020

# Square pixels first (sar applied, even width), then a copy at a quarter of the resolution.
_NORMALIZE = "scale=trunc(iw*sar/2)*2:ih,setsar=1"
_SMALL = f"{CANVAS_W // 4}:{CANVAS_H // 4}"  # the background is a quarter-resolution copy
_BACKGROUND = (
    f"scale={_SMALL}:force_original_aspect_ratio=increase,crop={_SMALL},boxblur=10:2,"
    f"scale={CANVAS_W}:{CANVAS_H}"
)
_RATIOS: dict[str, tuple[int, int]] = {"square": (1, 1), "portrait": (3, 4), "vertical": (9, 16)}
_UNESCAPED_COMMA = re.compile(r"(?<!\\),")
_BREAKS_GRAPH = re.compile(r"[:;\[\]'\"\\\s]")  # would end the option or open another filter


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def video_rect(layout: Layout, src_w: int, src_h: int) -> Rect:
    """Where the video zone sits on the canvas; `full` fits the whole picture in it."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"dimensions invalides : {src_w}x{src_h}")
    match layout:
        case "square":
            return Rect(0, VIDEO_TOP, CANVAS_W, CANVAS_W)
        case "portrait":
            return Rect(0, VIDEO_TOP, CANVAS_W, CANVAS_W * 4 // 3)
        case "vertical":
            return Rect(0, 0, CANVAS_W, CANVAS_H)
        case "full":
            height = min(_even(CANVAS_W * src_h / src_w), FULL_MAX_H)
            width = min(_even(height * src_w / src_h), CANVAS_W)
            return Rect((CANVAS_W - width) // 2, VIDEO_TOP, width, height)


def _crop(layout: str, crop_x: str | None) -> str:
    """Window of the layout's ratio, centred, or at the left edge `crop_x` (FFmpeg expression)."""
    num, den = _RATIOS[layout]
    if num == den:
        width = height = "min(iw\\,ih)"
    else:
        width, height = f"min(iw\\,ih*{num}/{den})", f"min(ih\\,iw*{den}/{num})"
    window = f"crop={width}:{height}"
    if crop_x is None:
        return window
    return f"{window}:{_UNESCAPED_COMMA.sub(r'\\,', crop_x)}:(ih-oh)/2"


def _check_expression(crop_x: str | None) -> None:
    if crop_x is not None and _BREAKS_GRAPH.search(crop_x.replace("\\,", "")):
        raise ValueError(f"crop_x : caractère interdit dans l'expression {crop_x!r}")


def plate_filter(layout: Layout, src_w: int, src_h: int, *, crop_x: str | None = None) -> str:
    """Filter graph from input `[0:v]` to the label `[v]` (1080x1920).

    `crop_x`: FFmpeg expression of the window's left edge, from the reframing (task 34); its
    commas are escaped here; a colon, semicolon, bracket, quote, backslash or space is refused
    (ValueError). None: centred. `full` never crops.
    """
    _check_expression(crop_x)
    rect = video_rect(layout, src_w, src_h)
    if layout == "full":
        foreground = f"scale={rect.w}:{rect.h}"
    else:
        foreground = f"{_crop(layout, crop_x)},scale={rect.w}:{rect.h}"
    if layout == "vertical":
        return f"[0:v]{_NORMALIZE},{foreground},setsar=1[v]"
    return (
        f"[0:v]{_NORMALIZE},split=2[bgin][fgin];"
        f"[bgin]{_BACKGROUND},setsar=1[bg];"
        f"[fgin]{foreground},setsar=1[fg];"
        f"[bg][fg]overlay={rect.x}:{rect.y}[v]"
    )
