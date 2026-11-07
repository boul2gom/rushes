"""Motion of a shot: mean absolute difference between successive gray samples."""

import itertools
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushes.core.intervals import Span
from rushes.media.frames import sample_gray


def shot_motion(
    path: Path,
    shots: Sequence[Span],
    *,
    fps: float = 4.0,
    width: int = 160,
    tmp_dir: Path | None = None,
) -> list[float]:
    """Per shot, in 0..1 (differences never cross a cut; under two samples gives 0.0).

    `shots` must be sorted and disjoint (ValueError otherwise); the samples come in time order,
    so one cursor walks both.
    """
    if any(a[1] > b[0] for a, b in itertools.pairwise(shots)):
        raise ValueError(f"plans triés et disjoints attendus : {list(shots)}")
    totals = [0.0] * len(shots)
    counts = [0] * len(shots)
    cursor = 0
    last: tuple[int, NDArray[np.int16]] | None = None  # (shot index, previous image)
    for t_ms, image in sample_gray(path, fps=fps, width=width, tmp_dir=tmp_dir):
        while cursor < len(shots) and shots[cursor][1] <= t_ms:
            cursor += 1
        if cursor == len(shots):
            break
        if t_ms < shots[cursor][0]:  # in a gap between two shots: no difference crosses it
            continue
        current = image.astype(np.int16)
        if last is not None and last[0] == cursor:
            totals[cursor] += float(np.mean(np.abs(current - last[1])))
            counts[cursor] += 1
        last = (cursor, current)
    pairs = zip(totals, counts, strict=True)
    return [total / count / 255 if count else 0.0 for total, count in pairs]
