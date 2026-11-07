"""Audio decoding for the analysis models."""

import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushes.core.intervals import Span
from rushes.media.ffmpeg import run_ffmpeg

SAMPLE_RATE = 16_000


def decode_pcm16k(
    path: Path, *, span: Span | None = None, tmp_dir: Path | None = None
) -> NDArray[np.float32]:
    """Mono float32 samples at 16 kHz (optionally only `span`, in ms); MediaError if undecodable.

    The raw samples go through a temporary file under `tmp_dir` (default: the system one)."""
    args: list[str | Path] = []
    if span is not None:
        args += ["-ss", f"{span[0] / 1000:.3f}", "-t", f"{(span[1] - span[0]) / 1000:.3f}"]
    args += ["-i", path, "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE)]
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir, prefix="audio-") as folder:
        raw = Path(folder) / "audio.f32"
        run_ffmpeg([*args, "-f", "f32le", raw])
        return np.fromfile(raw, dtype="<f4").astype(np.float32, copy=False)
