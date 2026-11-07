"""Integrated and short-term loudness with ffmpeg's ebur128 filter.

Time of a point: ebur128 prints `t` at the end of its 3 s short-term window, so a point at `t_ms`
describes the sound in [t_ms - 3000, t_ms]. Nothing is shifted here (decision C42).
"""

import re
from pathlib import Path

from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe
from rushes.schemas import LoudPoint

FLOOR_LUFS = -70.0
_FRAME = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bS:\s*(-inf|-?\d+(?:\.\d+)?)")
_INTEGRATED = re.compile(r"^\s+I:\s+(-inf|-?\d+(?:\.\d+)?)\s+LUFS", re.MULTILINE)


def _lufs(value: str) -> float:
    return FLOOR_LUFS if value == "-inf" else max(float(value), FLOOR_LUFS)


def loudness_track(path: Path) -> tuple[float, list[LoudPoint]]:
    """(integrated LUFS, short-term LUFS every 100 ms); (-70.0, []) without an audio track."""
    if not probe(path).has_audio:
        return FLOOR_LUFS, []
    stderr = run_ffmpeg(
        ["-v", "verbose", "-i", path, "-vn", "-af", "ebur128=framelog=verbose", "-f", "null", "-"]
    )
    points = [
        LoudPoint(t_ms=round(float(m.group(1)) * 1000), lufs=_lufs(m.group(2)))
        for m in map(_FRAME.search, stderr.splitlines())
        if m
    ]
    summary = _INTEGRATED.findall(stderr)
    return (_lufs(summary[-1]) if summary else FLOOR_LUFS), points
