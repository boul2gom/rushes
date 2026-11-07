"""Partition of an episode into parts by dynamic programming (spec, Partition en parties)."""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise

from rushes.core.intervals import Span


def _validate(
    points_ms: Sequence[int],
    point_cost: Sequence[float],
    duration_ms: int,
    min_ms: int,
    max_ms: int,
) -> None:
    if len(points_ms) != len(point_cost):
        raise ValueError("autant de coûts que de points attendus")
    if not points_ms or points_ms[0] != 0:
        raise ValueError("le premier point doit être 0")
    if any(a >= b for a, b in pairwise(points_ms)):
        raise ValueError("points non triés ou en double")
    if points_ms[-1] > duration_ms:
        raise ValueError(f"point après la fin de l'épisode : {points_ms[-1]} > {duration_ms}")
    if not 0 < min_ms <= max_ms:
        raise ValueError(f"bornes invalides : {min_ms}..{max_ms}")


def partition(
    points_ms: Sequence[int],
    point_cost: Sequence[float],
    *,
    duration_ms: int,
    targets_ms: Sequence[int],
    min_ms: int,
    max_ms: int,
    target_weight: float,
) -> list[Span]:
    """Consecutive parts of `min_ms..max_ms` between candidate points, or [] when none fits.

    dp[k][j] is the least cost to cover [0, p_j] in k parts: the cost of the cut point j plus
    `target_weight` per squared second between the part length and its target. The end is the
    admissible point (less than `min_ms` before `duration_ms`) that covers the most, then the
    cheapest, then with the fewest parts: a too short tail is merged into the previous part when
    `max_ms` allows it, else dropped. A point costing inf is never used; the point at the
    duration costs 0.
    """
    _validate(points_ms, point_cost, duration_ms, min_ms, max_ms)
    cost = [0.0 if p == duration_ms else c for p, c in zip(points_ms, point_cost, strict=True)]
    max_parts = min(len(targets_ms), duration_ms // min_ms)
    layers: list[dict[int, tuple[float, int]]] = [{0: (0.0, -1)}]  # j -> (cost, previous j)
    for k in range(1, max_parts + 1):
        layer: dict[int, tuple[float, int]] = {}
        for i, (base, _) in layers[-1].items():
            lo = bisect_left(points_ms, points_ms[i] + min_ms)
            hi = bisect_right(points_ms, points_ms[i] + max_ms)
            for j in range(lo, hi):
                if math.isinf(cost[j]):
                    continue
                gap = (points_ms[j] - points_ms[i] - targets_ms[k - 1]) / 1000
                total = base + cost[j] + target_weight * gap * gap
                if j not in layer or total < layer[j][0]:
                    layer[j] = (total, i)
        layers.append(layer)

    best: tuple[int, float, int, int] | None = None  # (-covered, cost, parts, j), smallest wins
    for k, layer in enumerate(layers[1:], start=1):
        for j, (total, _) in layer.items():
            if duration_ms - points_ms[j] < min_ms:
                candidate = (-points_ms[j], total, k, j)
                if best is None or candidate < best:
                    best = candidate
    if best is None:
        return []
    _, _, k, j = best
    bounds = [points_ms[j]]
    while k > 0:
        j = layers[k][j][1]
        bounds.append(points_ms[j])
        k -= 1
    bounds.reverse()
    return list(pairwise(bounds))


def shift_weak_starts(
    parts: Sequence[Span],
    *,
    shot_starts_ms: Sequence[int],
    cost_of: Mapping[int, float],
    motion_after: Callable[[int], float],
    min_ms: int,
    max_ms: int,
    motion_min: float,
    shift_ms: int,
) -> list[Span]:
    """Move a part start without motion to the next shot start, when the parts stay in bounds.

    For each part start p (0 included) with `motion_after(p) < motion_min`, the first shot start
    in ]p, p + shift_ms] with a finite cost in `cost_of` (a point never costed is not a valid
    boundary) becomes the new boundary (end of the previous part
    too); if a part would leave `min_ms..max_ms`, nothing moves.
    """
    starts = [start for start, _ in parts]
    ends = [end for _, end in parts]
    shots = sorted(shot_starts_ms)
    for index, p in enumerate(starts):
        if motion_after(p) >= motion_min:
            continue
        window = shots[bisect_right(shots, p) : bisect_right(shots, p + shift_ms)]
        moved = next((s for s in window if not math.isinf(cost_of.get(s, math.inf))), None)
        if moved is None or not min_ms <= ends[index] - moved <= max_ms:
            continue
        if index > 0 and not min_ms <= moved - starts[index - 1] <= max_ms:
            continue
        starts[index] = moved
        if index > 0:
            ends[index - 1] = moved
    return list(zip(starts, ends, strict=True))
