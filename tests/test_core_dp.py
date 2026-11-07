"""Part partition (task 6): cut point costs, dynamic programming, hook-friendly starts."""

import math
import random
from collections.abc import Callable, Sequence
from itertools import pairwise

import pytest

from rushes.config import CutSettings
from rushes.core.dp import partition, shift_weak_starts
from rushes.core.intervals import Span
from rushes.core.rng import derive_seed, draw_target_ms
from rushes.core.scoring import cut_point_costs, quiet_points

INF = math.inf
MIN_MS, MAX_MS = 65_000, 100_000


def _no_motion(_: int) -> float:
    return 0.0


def _costs(
    points: Sequence[int],
    *,
    loudness: Sequence[tuple[int, float]] = (),
    speech: Sequence[Span] = (),
    peaks: Sequence[int] = (),
    motion: Callable[[int], float] = _no_motion,
    settings: CutSettings | None = None,
) -> list[float]:
    return cut_point_costs(
        points,
        loudness=loudness,
        speech=speech,
        peaks_ms=peaks,
        motion_after=motion,
        s=settings or CutSettings(),
    )


def _bounds(parts: Sequence[Span]) -> list[int]:
    return [parts[0][0], *(end for _, end in parts)]


# --- partition: eight hand-written cases (min 10 s, max 20 s) ----------------------------------
# columns: points, costs, duration, targets, expected, target_weight
REFERENCE_CASES = [
    pytest.param(  # 0: all costs equal, targets met exactly by three equal parts
        [0, 10_000, 20_000, 30_000],
        [0.0, 0.0, 0.0, 0.0],
        30_000,
        [10_000] * 4,
        [(0, 10_000), (10_000, 20_000), (20_000, 30_000)],
        1.0,
        id="targets-met",
    ),
    pytest.param(  # 1: a deep trough at 18 s beats the 15 s targets (weight 0.01 is small)
        [0, 12_000, 15_000, 18_000, 30_000],
        [0.0, -2.0, 1.0, -3.0, 0.0],
        30_000,
        [15_000, 15_000],
        [(0, 18_000), (18_000, 30_000)],
        0.01,
        id="cost-beats-target",
    ),
    pytest.param(  # 2: equal costs, the targets pull the cut to 18 s
        [0, 12_000, 15_000, 18_000, 30_000],
        [0.0, 0.0, 0.0, 0.0, 0.0],
        30_000,
        [18_000, 12_000],
        [(0, 18_000), (18_000, 30_000)],
        1.0,
        id="target-pulls",
    ),
    pytest.param(  # 3: the 5 s tail cannot be a part, 20 s fits the last part: merged
        [0, 15_000, 30_000, 35_000],
        [0.0, 0.0, 0.0, 0.0],
        35_000,
        [15_000, 15_000],
        [(0, 15_000), (15_000, 35_000)],
        1.0,
        id="tail-merged",
    ),
    pytest.param(  # 4: no way to cover the episode with parts of 10 to 20 s
        [0, 25_000, 50_000],
        [0.0, 0.0, 0.0],
        50_000,
        [15_000] * 6,
        [],
        1.0,
        id="no-solution",
    ),
    pytest.param(  # 5: forbidden points (inf) are never used
        [0, 10_000, 15_000, 20_000, 30_000],
        [0.0, INF, 0.0, INF, 0.0],
        30_000,
        [10_000, 10_000, 10_000, 10_000],
        [(0, 15_000), (15_000, 30_000)],
        1.0,
        id="forbidden-points",
    ),
    pytest.param(  # 6: a tail of exactly min_ms (10 s) is a part, not a remainder to drop
        [0, 20_000, 30_000],
        [0.0, 0.0, 0.0],
        30_000,
        [20_000, 10_000],
        [(0, 20_000), (20_000, 30_000)],
        1.0,
        id="tail-exactly-min-is-a-part",
    ),
    pytest.param(  # 7: coverage and cost tie (weight 0): fewer parts wins, then the earliest cut
        [0, 10_000, 20_000, 30_000],
        [0.0, 0.0, 0.0, 0.0],
        30_000,
        [10_000] * 4,
        [(0, 10_000), (10_000, 30_000)],
        0.0,
        id="tie-fewer-parts",
    ),
    pytest.param(  # 8: a 10 s remainder (= min_ms) is not droppable, and no point covers it
        [0, 10_000, 20_000],
        [0.0, 0.0, 0.0],
        30_000,
        [10_000] * 4,
        [],
        1.0,
        id="tail-exactly-min-is-not-dropped",
    ),
]


@pytest.mark.parametrize(
    ("points", "costs", "duration", "targets", "expected", "weight"), REFERENCE_CASES
)
def test_partition_reference_cases(
    points: list[int],
    costs: list[float],
    duration: int,
    targets: list[int],
    expected: list[Span],
    weight: float,
) -> None:
    got = partition(
        points,
        costs,
        duration_ms=duration,
        targets_ms=targets,
        min_ms=10_000,
        max_ms=20_000,
        target_weight=weight,
    )
    assert got == expected


def test_partition_episode_shorter_than_min_gives_nothing() -> None:
    assert (
        partition(
            [0, 8_000],
            [0.0, 0.0],
            duration_ms=8_000,
            targets_ms=[10_000],
            min_ms=10_000,
            max_ms=20_000,
            target_weight=1.0,
        )
        == []
    )


def test_partition_rejects_unsorted_points() -> None:
    with pytest.raises(ValueError):
        partition(
            [0, 20_000, 10_000],
            [0.0] * 3,
            duration_ms=30_000,
            targets_ms=[10_000],
            min_ms=10_000,
            max_ms=20_000,
            target_weight=1.0,
        )


def test_partition_rejects_a_point_past_the_duration() -> None:
    with pytest.raises(ValueError):
        partition(
            [0, 10_000, 40_000],
            [0.0] * 3,
            duration_ms=30_000,
            targets_ms=[10_000, 10_000],
            min_ms=10_000,
            max_ms=20_000,
            target_weight=1.0,
        )


def test_tail_dropped_when_too_short() -> None:
    parts = partition(
        [0, 95_000, 190_000, 200_000],
        [0.0, 0.0, 0.0, 0.0],
        duration_ms=200_000,
        targets_ms=[95_000, 95_000, 95_000],
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        target_weight=1.0,
    )
    assert parts == [(0, 95_000), (95_000, 190_000)]  # 105 s > max: no merge


def test_tail_merged_when_max_allows() -> None:
    parts = partition(
        [0, 75_000, 140_000, 150_000],
        [0.0, 0.0, 0.0, 0.0],
        duration_ms=150_000,
        targets_ms=[75_000, 75_000, 75_000],
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        target_weight=1.0,
    )
    assert parts == [(0, 75_000), (75_000, 150_000)]  # the 10 s tail is merged, never dropped


# --- R5 ---------------------------------------------------------------------------------------


def test_r5_no_cut_during_speech() -> None:
    points = list(range(0, 150_001, 150))
    speech = [(60_000, 75_000)]  # margin 150 ms: [59850, 75150] is forbidden
    costs = _costs(points, speech=speech)
    forbidden = [p for p in points if 59_850 <= p <= 75_150]
    assert forbidden and all(costs[points.index(p)] == INF for p in forbidden)
    assert costs[points.index(59_700)] != INF and costs[points.index(75_300)] != INF
    parts = partition(
        points,
        costs,
        duration_ms=150_000,
        targets_ms=[70_000, 80_000],
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        target_weight=1.0,
    )
    assert parts
    assert not any(59_850 <= b <= 75_150 for b in _bounds(parts))


def test_r5_no_cut_1500ms_before_peak() -> None:
    points = list(range(0, 160_001, 500))
    costs = _costs(points, peaks=[80_000])
    for p in points:
        in_guard = 78_500 <= p < 80_000
        assert (costs[points.index(p)] == INF) is (in_guard and p not in (0, 160_000))
    parts = partition(
        points,
        costs,
        duration_ms=160_000,
        targets_ms=[79_000, 81_000],  # the natural cut would fall in the guard
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        target_weight=1.0,
    )
    assert parts
    assert not any(78_500 <= b < 80_000 for b in _bounds(parts))
    assert _bounds(parts)[1] in (78_000, 80_000)


# --- cut_point_costs ---------------------------------------------------------------------------


def test_cut_point_costs_loudness_is_a_z_score_with_troughs_negative() -> None:
    points = [0, 1000, 2000, 3000, 4000]
    loudness = [(0, -20.0), (1000, -20.0), (2000, -10.0), (3000, -20.0), (4000, -20.0)]
    costs = _costs(points, loudness=loudness)
    # mean -18, std 4: z(-20) = -0.5, z(-10) = 2; first and last points cost 0
    assert costs == pytest.approx([0.0, -0.5, 2.0, -0.5, 0.0])


def test_cut_point_costs_uses_the_nearest_loudness_point_clamped() -> None:
    loudness = [(1500, -30.0), (2500, -10.0)]  # mean -20, std 10
    costs = _costs([0, 100, 2400, 9000, 10_000], loudness=loudness)
    assert costs[1] == pytest.approx(-1.0)  # before the first point: clamped to it
    assert costs[2] == pytest.approx(1.0)  # nearest is 2500
    assert costs[3] == pytest.approx(1.0)  # after the last point: clamped to it


def test_cut_point_costs_without_loudness_or_motion_is_zero() -> None:
    assert _costs([0, 500, 1000, 1500]) == [0.0, 0.0, 0.0, 0.0]


def test_cut_point_costs_motion_after_earns_a_bonus() -> None:
    costs = _costs([0, 1000, 2000], motion=lambda p: 1.0)
    assert costs == pytest.approx([0.0, -0.5, 0.0])  # motion_start_bonus 0.5


def test_cut_point_costs_weights_come_from_the_settings() -> None:
    settings = CutSettings(loud_weight=2.0, motion_start_bonus=0.0, speech_margin_ms=500)
    loudness = [(1000, -20.0), (2000, -10.0)]
    speech = [(1800, 2000)]  # widened by 500 ms to [1300, 2500]
    costs = _costs([0, 1000, 2000, 3000], loudness=loudness, settings=settings, speech=speech)
    assert costs[1] == pytest.approx(-2.0)  # z = -1, weight 2
    assert costs[2] == INF
    assert costs[3] == 0.0


def test_quiet_points_are_the_loudness_minimum_of_each_step() -> None:
    loudness = [(t, -20.0) for t in range(0, 6001, 500)]
    loudness[1] = (500, -40.0)  # trough in [0, 2000[
    loudness[6] = (3000, -35.0)  # trough in [2000, 4000[
    assert quiet_points(loudness, step_ms=2000, duration_ms=6000) == [500, 3000, 4000]


def test_quiet_points_stay_strictly_inside_the_episode() -> None:
    loudness = [(0, -50.0), (6000, -50.0)]
    assert quiet_points(loudness, step_ms=2000, duration_ms=6000) == []


# --- R6 ---------------------------------------------------------------------------------------


def test_r6_segment_out_of_bounds_never_proposed() -> None:
    parts_seen = 0
    for episode in range(200):
        rng = random.Random(episode)
        duration = rng.randint(30_000, 400_000)
        points = [0]
        while points[-1] + 8000 < duration:
            points.append(points[-1] + rng.randint(1000, 8000))
        points.append(duration)
        costs = [rng.choice([INF, rng.uniform(-1.0, 2.0)]) for _ in points]
        targets = [
            draw_target_ms(derive_seed(episode, "cut", k), 65, 100)
            for k in range(duration // MIN_MS + 1)
        ]
        parts = partition(
            points,
            costs,
            duration_ms=duration,
            targets_ms=targets,
            min_ms=MIN_MS,
            max_ms=MAX_MS,
            target_weight=1.0,
        )
        for (_, end), (next_start, _) in pairwise(parts):
            assert end == next_start
        for start, end in parts:
            assert MIN_MS <= end - start <= MAX_MS
            assert costs[points.index(end)] != INF or end == duration
        if parts:
            assert parts[0][0] == 0 and duration - parts[-1][1] < MIN_MS
        parts_seen += len(parts)
    assert parts_seen > 100  # the property is not vacuous


# --- hook-friendly starts ----------------------------------------------------------------------


def _free(points: Sequence[int]) -> dict[int, float]:
    return dict.fromkeys(points, 0.0)


def test_start_shifted_to_next_shot() -> None:
    shot_starts = [0, 35_000, 70_000, 71_000, 110_000]
    parts = shift_weak_starts(
        [(0, 70_000), (70_000, 150_000)],
        shot_starts_ms=shot_starts,
        cost_of=_free(shot_starts),
        motion_after=lambda p: 0.0 if 70_000 <= p < 72_000 else 0.5,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    assert parts == [(0, 71_000), (71_000, 150_000)]  # end of part 1 == start of part 2 == 71 s


def test_start_not_shifted_when_a_part_would_leave_its_bounds() -> None:
    parts = shift_weak_starts(
        [(0, 100_000), (100_000, 170_000)],
        shot_starts_ms=[0, 100_000, 101_000],
        cost_of=_free([0, 100_000, 101_000]),
        motion_after=lambda p: 0.0,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    # part 1 would last 101 s (> max): nothing moves at 100 s; no shot follows the start 0
    assert parts == [(0, 100_000), (100_000, 170_000)]


def test_start_not_shifted_into_a_forbidden_point() -> None:
    parts = shift_weak_starts(
        [(0, 70_000), (70_000, 150_000)],
        shot_starts_ms=[0, 71_000],
        cost_of={0: 0.0, 71_000: INF},
        motion_after=lambda p: 0.0,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    assert parts == [(0, 70_000), (70_000, 150_000)]


def test_first_start_can_move_to_the_next_shot() -> None:
    parts = shift_weak_starts(
        [(0, 70_000)],
        shot_starts_ms=[0, 1500],
        cost_of=_free([0, 1500]),
        motion_after=lambda p: 0.0,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    assert parts == [(1500, 70_000)]


def test_start_not_shifted_when_the_part_would_shrink_below_min() -> None:
    parts = shift_weak_starts(
        [(0, 70_000), (70_000, 135_500)],
        shot_starts_ms=[0, 70_000, 71_000],
        cost_of=_free([0, 70_000, 71_000]),
        motion_after=lambda p: 0.5 if p == 0 else 0.0,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    # moving to 71 s would leave 64.5 s to the second part (< min): nothing moves
    assert parts == [(0, 70_000), (70_000, 135_500)]


def test_start_not_shifted_to_a_point_that_was_never_costed() -> None:
    parts = shift_weak_starts(
        [(0, 70_000), (70_000, 150_000)],
        shot_starts_ms=[0, 71_000],
        cost_of={0: 0.0},  # 71 s has no cost: not a valid boundary
        motion_after=lambda p: 0.0,
        min_ms=MIN_MS,
        max_ms=MAX_MS,
        motion_min=0.15,
        shift_ms=2000,
    )
    assert parts == [(0, 70_000), (70_000, 150_000)]
