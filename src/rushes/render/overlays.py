"""Overlays pass (task 7b): the plate plus PNG overlays and the progress bar, in one encode.

Every text is a Pillow PNG fed to FFmpeg as an input: no text, no file path ever sits in a filter
string (the Mac build has no `drawtext`, none of `subtitles`/`ass` is used). The sound of the plate
is copied, so the loudness of R7 is untouched.
"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from rushes.config import RenderSettings
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.verify import FRAME_RATE
from rushes.originality.base import RenderJob, RenderPlan, job_texts
from rushes.render.canvas import Rect
from rushes.render.encode import CONTAINER_ARGS, video_args

RENDER_OVERLAY_VERSION = "ov-1"  # bump when an overlay looks different: rendered posts redo it
BAR_PX = 12
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def overlay_texts(job: RenderJob) -> list[str]:
    """Every text drawn on the video of `job` (R9 tests read it; the module draws exactly these)."""
    return job_texts(job).all()


def overlay_digest(job: RenderJob) -> str:
    """Fingerprint of everything written on the video of `job` (numbers, hook, next line...).

    A rendered post is stale when its digest differs from the one stored at render time (task 16b
    stores it; `render` writes it in its guarded UPDATE once the column exists)."""
    return hashlib.sha1("\n".join(overlay_texts(job)).encode(), usedforsecurity=False).hexdigest()


def _seconds(ms: int) -> str:
    """A plain number (no locale): FFmpeg expressions need a dot."""
    return f"{ms / 1000:.3f}"


def render_overlays(
    plate: Path,
    plan: RenderPlan,
    out: Path,
    *,
    duration_ms: int,
    bar_rect: Rect,
    bar_color: str,
    s: RenderSettings,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    """Write `plate` with the overlays of `plan` (and the bar under the picture) into `out`.

    `out` is written as is: the caller hands a temporary path. The audio is copied."""
    if plan.audio_layers:
        raise NotImplementedError("tâche 35")
    if not _COLOR.fullmatch(bar_color):
        raise ValueError(f"couleur #RRGGBB attendue : {bar_color}")
    if duration_ms <= 0:
        raise ValueError(f"durée invalide : {duration_ms} ms")
    total = _seconds(duration_ms)
    args: list[str | Path] = ["-i", plate]
    graph: list[str] = []
    label = "[0:v]"
    for index, overlay in enumerate(plan.overlays, start=1):
        args += ["-loop", "1", "-framerate", str(FRAME_RATE), "-t", total, "-i", overlay.png]
        window = f"between(t,{_seconds(overlay.start_ms)},{_seconds(overlay.end_ms)})"
        step = f"[v{index}]"
        graph.append(
            f"{label}[{index}:v]overlay=x={overlay.x}:y={overlay.y}:enable='{window}':"
            f"format=auto{step}"
        )
        label = step
    if plan.progress_bar:
        index = len(plan.overlays) + 1
        source = f"color=c=0x{bar_color[1:]}:s={bar_rect.w}x{bar_rect.h}:r={FRAME_RATE}:d={total}"
        args += ["-f", "lavfi", "-i", source]
        graph.append(
            f"{label}[{index}:v]overlay=x='-W+W*t/{total}':y={bar_rect.y}:format=auto[vbar]"
        )
        label = "[vbar]"
    graph.append(f"{label}null[v]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "0:a:0"]
    args += ["-map_metadata", "-1", "-t", total]
    args += [*video_args(s), "-c:a", "copy", *CONTAINER_ARGS, "-f", "mp4", out]
    run_ffmpeg(args, duration_ms=duration_ms, on_progress=on_progress)
