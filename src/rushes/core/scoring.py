"""Cost of a cut point and fallback quiet points (spec, Partition en parties, steps 1 and 2)."""

import math
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence

from rushes.config import CutSettings
from rushes.core.intervals import Span


def _zscores(loudness: Sequence[tuple[int, float]]) -> list[float]:
    values = [lufs for _, lufs in loudness]
    spread = statistics.pstdev(values)
    mean = statistics.fmean(values)
    return [(v - mean) / spread if spread > 0 else 0.0 for v in values]


def _merged(spans: Sequence[Span]) -> list[Span]:
    """Sorted union of closed spans (overlapping or touching ones are joined)."""
    merged: list[Span] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def cut_point_costs(
    points_ms: Sequence[int],
    *,
    loudness: Sequence[tuple[int, float]],
    speech: Sequence[Span],
    peaks_ms: Sequence[int],
    motion_after: Callable[[int], float],
    s: CutSettings,
) -> list[float]:
    """Cost of cutting at each point; inf when forbidden (R5), lower is better.

    `loudness` holds (centre time, short-term LUFS) pairs. A point costs `loud_weight` times the
    z-score (over the episode) of the loudness nearest to it, minus `motion_start_bonus` times the
    motion of the 2 s that follow. It is inf inside a speech span widened by `speech_margin_ms`, or
    when a peak lies in ]p, p + peak_guard_ms]. The first and last points cost 0.
    """
    curve = sorted(loudness)
    times = [t for t, _ in curve]
    zscores = _zscores(curve) if curve else []
    forbidden = _merged([(a - s.speech_margin_ms, b + s.speech_margin_ms) for a, b in speech])
    forbidden_starts = [a for a, _ in forbidden]
    peaks = sorted(peaks_ms)

    def cost(p: int) -> float:
        at = bisect_right(forbidden_starts, p) - 1
        if at >= 0 and p <= forbidden[at][1]:  # closed span
            return math.inf
        if bisect_right(peaks, p + s.peak_guard_ms) > bisect_right(peaks, p):
            return math.inf
        total = -s.motion_start_bonus * motion_after(p)
        if curve:
            at = bisect_left(times, p)
            near = min(
                (i for i in (at - 1, at) if 0 <= i < len(times)), key=lambda i: abs(times[i] - p)
            )
            total += s.loud_weight * zscores[near]
        return total

    costs = [cost(p) for p in points_ms]
    if costs:
        costs[0] = costs[-1] = 0.0
    return costs


def quiet_points(
    loudness: Sequence[tuple[int, float]], *, step_ms: int, duration_ms: int
) -> list[int]:
    """Fallback cut points: the quietest instant of each `step_ms` window, inside the episode.

    Used when the shot starts leave no admissible partition (no shot change in a long scene).
    """
    troughs: dict[int, tuple[float, int]] = {}
    for t, lufs in sorted(loudness):
        if 0 < t < duration_ms:
            window = t // step_ms
            if window not in troughs or lufs < troughs[window][0]:
                troughs[window] = (lufs, t)
    return sorted(t for _, t in troughs.values())
