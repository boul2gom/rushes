"""Delivery encoding (R7) and the plate render: picture on the canvas plus normalized sound."""

from collections.abc import Callable
from pathlib import Path

from rushes.config import Layout, RenderSettings
from rushes.core.intervals import Span
from rushes.errors import MediaError
from rushes.fsutil import atomic_path
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe
from rushes.media.verify import FRAME_RATE, SAMPLE_RATE, SILENT_TRACK
from rushes.render.canvas import plate_filter
from rushes.render.loudnorm import (
    PLAIN_RESAMPLE,
    PlateReport,
    measure,
    parse_report,
    second_pass_filter,
)

DURATION_TOLERANCE_MS = 150  # video frame (33 ms) plus AAC frames and start-up of the encoder


def encode_args(s: RenderSettings) -> list[str]:
    """H.264 High 8 bit yuv420p at a constant 30 i/s, AAC 48 kHz stereo, +faststart.

    `-profile:v` only caps the profile, and the fast presets switch the 8x8 transform off, which
    makes x264 write Constrained Baseline: `8x8dct=1` keeps it High at every preset (R7)."""
    return [
        *("-c:v", "libx264", "-preset", s.preset, "-crf", str(s.crf), "-profile:v", "high"),
        *("-x264-params", "8x8dct=1"),
        *("-pix_fmt", "yuv420p", "-r", str(FRAME_RATE), "-fps_mode", "cfr"),
        *("-c:a", "aac", "-b:a", "160k", "-ar", str(SAMPLE_RATE), "-ac", "2"),
        *("-movflags", "+faststart", "-threads", "4"),
    ]


def render_plate(
    src: Path,
    span: Span,
    layout: Layout,
    out: Path,
    *,
    s: RenderSettings,
    crop_x: str | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> PlateReport | None:
    """Render `span` (ms) of `src` on the 9:16 canvas with two-pass loudnorm into `out`.

    Silent audio gets no gain; a source without audio gets a silent stereo track, so the
    delivery always holds AAC: both return None. Otherwise the loudness the second pass
    reached and its mode are returned (the caller checks them against R7). A render shorter than
    the span (it runs past the end of `src`) is a MediaError. `out` is written atomically.
    """
    length_ms = span[1] - span[0]
    if length_ms <= 0:
        raise MediaError(f"durée de rendu invalide : {span[0]}-{span[1]} ms")
    info = probe(src)
    graph = plate_filter(layout, info.display_width, info.height, crop_x=crop_x)
    args: list[str | Path] = [
        *("-ss", f"{span[0] / 1000:.3f}", "-t", f"{length_ms / 1000:.3f}", "-i", src),
    ]
    reports = False
    if info.has_audio:
        gain = measure(src, span)
        reports = not gain.silent
        audio = PLAIN_RESAMPLE if gain.silent else second_pass_filter(gain, report=True)
        audio_in = "[0:a:0]"
    else:
        args += ["-f", "lavfi", "-i", SILENT_TRACK]
        audio, audio_in = PLAIN_RESAMPLE, "[1:a]"
    # apad makes the sound endless; -shortest and -t bound the output to the picture
    graph += f";{audio_in}{audio},apad[a]"
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
    args += ["-shortest", "-t", f"{length_ms / 1000:.3f}"]
    with atomic_path(out) as tmp:
        log = run_ffmpeg(
            [*args, *encode_args(s), "-f", "mp4", tmp],
            duration_ms=length_ms,
            on_progress=on_progress,
        )
        made_ms = probe(tmp).duration_ms
        if abs(made_ms - length_ms) > DURATION_TOLERANCE_MS:
            raise MediaError(
                f"durée rendue {made_ms} ms au lieu de {length_ms} ms attendus "
                f"(le passage dépasse la fin de {src.name} ?)"
            )
        return parse_report(log) if reports else None  # before the rename: no file if absent
