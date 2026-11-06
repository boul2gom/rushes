"""Black, silence and frozen-picture detectors: pure log parsers, then the ffmpeg calls."""

import re
from pathlib import Path

from rushes.core.intervals import Span
from rushes.core.timecode import s_to_ms
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe

_NUMBER = r"(-?\d+(?:\.\d+)?)"
_BLACK = re.compile(rf"black_start:{_NUMBER}\s+black_end:{_NUMBER}")
_SILENCE_START = re.compile(rf"silence_start:\s*{_NUMBER}")
_SILENCE_END = re.compile(rf"silence_end:\s*{_NUMBER}")
_FREEZE_START = re.compile(rf"freeze_start:\s*{_NUMBER}")
_FREEZE_END = re.compile(rf"freeze_end:\s*{_NUMBER}")


def _span(start_s: float, end_s: float) -> Span | None:
    start, end = max(0, s_to_ms(start_s)), s_to_ms(end_s)
    return (start, end) if end > start else None


def parse_blackdetect(stderr: str) -> list[Span]:
    spans = (_span(float(a), float(b)) for a, b in _BLACK.findall(stderr))
    return [span for span in spans if span is not None]


def _pairs(
    stderr: str, start: re.Pattern[str], end: re.Pattern[str], end_ms: int | None
) -> list[Span]:
    """Pair start/end lines in order; a start left open is closed at `end_ms` when given."""
    spans: list[Span] = []
    opened: float | None = None
    for line in stderr.splitlines():
        if (m := start.search(line)) is not None:
            opened = float(m.group(1))
        elif (m := end.search(line)) is not None and opened is not None:
            span = _span(opened, float(m.group(1)))
            opened = None
            if span is not None:
                spans.append(span)
    if opened is not None and end_ms is not None:
        span = _span(opened, end_ms / 1000)
        if span is not None:
            spans.append(span)
    return spans


def parse_silencedetect(stderr: str, *, end_ms: int | None = None) -> list[Span]:
    return _pairs(stderr, _SILENCE_START, _SILENCE_END, end_ms)


def parse_freezedetect(stderr: str, *, end_ms: int | None = None) -> list[Span]:
    return _pairs(stderr, _FREEZE_START, _FREEZE_END, end_ms)


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _end(path: Path, duration_ms: int | None) -> int:
    return probe(path).duration_ms if duration_ms is None else duration_ms


def detect_black(path: Path, *, min_ms: int) -> list[Span]:
    log = run_ffmpeg(
        [
            "-i",
            path,
            "-an",
            "-vf",
            f"blackdetect=d={_seconds(min_ms)}:pix_th=0.10",
            "-f",
            "null",
            "-",
        ]
    )
    return parse_blackdetect(log)


def detect_silence(
    path: Path, *, min_ms: int, noise_db: float = -50.0, duration_ms: int | None = None
) -> list[Span]:
    """`duration_ms` closes a silence still open at the end; probed when not given."""
    flt = f"silencedetect=noise={noise_db}dB:d={_seconds(min_ms)}"
    log = run_ffmpeg(["-i", path, "-vn", "-af", flt, "-f", "null", "-"])
    return parse_silencedetect(log, end_ms=_end(path, duration_ms))


def detect_freeze(
    path: Path, *, min_ms: int, noise: float = 0.003, duration_ms: int | None = None
) -> list[Span]:
    """`duration_ms` closes a freeze still open at the end; probed when not given."""
    log = run_ffmpeg(
        [
            "-i",
            path,
            "-an",
            "-vf",
            f"freezedetect=n={noise}:d={_seconds(min_ms)}",
            "-f",
            "null",
            "-",
        ]
    )
    return parse_freezedetect(log, end_ms=_end(path, duration_ms))
