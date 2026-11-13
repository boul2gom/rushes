"""Compilation splitting rules (task 11, C50): pure functions, no media."""

from itertools import pairwise

from rushes.clean.splitter import (
    MERGE_GAP_MS,
    MERGE_MAX_MS,
    Boundary,
    black_boundaries,
    split_spans,
)
from rushes.config import CleanSettings
from rushes.core.intervals import Span

# 20 s minimum episode, 60 s to look at a video, 45 s maximum, 30 s typical
S = CleanSettings(episode_min_s=20, compilation_min_s=60, episode_max_s=45, typical_episode_s=30)
MS = 1000


def _gap(at_ms: int, width_ms: int = 1000, *, silent: bool = False) -> Boundary:
    """A black of `width_ms` centred on `at_ms`."""
    return Boundary(at_ms - width_ms // 2, at_ms + width_ms // 2, silent)


# --- black_boundaries -------------------------------------------------------------------------


def test_boundaries_are_the_blacks_of_at_least_min_ms() -> None:
    blacks: list[Span] = [(10_000, 12_000), (30_000, 30_400), (50_000, 50_500)]
    found = black_boundaries(blacks, [], min_ms=500)
    assert found == [Boundary(10_000, 12_000, False), Boundary(50_000, 50_500, False)]


def test_a_black_alone_is_a_boundary_a_silence_alone_is_not() -> None:
    assert black_boundaries([(0, 5000)], [], min_ms=500) == [Boundary(0, 5000, False)]
    assert black_boundaries([], [(0, 5000)], min_ms=500) == []


def test_a_black_overlapping_a_silence_is_marked_silent() -> None:
    blacks: list[Span] = [(10_000, 12_000)]
    assert black_boundaries(blacks, [(11_500, 13_000)], min_ms=500)[0].silent  # 500 ms in common
    assert not black_boundaries(blacks, [(11_600, 13_000)], min_ms=500)[0].silent  # 400 ms
    assert not black_boundaries(blacks, [(20_000, 21_000)], min_ms=500)[0].silent


def test_blacks_around_a_title_card_are_one_boundary() -> None:
    blacks: list[Span] = [(10_000, 11_000), (10_000 + 1000 + MERGE_GAP_MS, 20_000)]
    assert black_boundaries(blacks, [], min_ms=500) == [Boundary(10_000, 20_000, False)]
    apart: list[Span] = [(10_000, 11_000), (11_000 + MERGE_GAP_MS + 1, 20_000)]
    assert len(black_boundaries(apart, [], min_ms=500)) == 2


def test_a_run_of_blacks_every_few_seconds_is_not_one_huge_boundary() -> None:
    # a flashing montage: 0.6 s of black every 6 s for 300 s
    blacks: list[Span] = [(t, t + 600) for t in range(400_000, 700_000, 6000)]
    found = black_boundaries(blacks, [], min_ms=500)
    assert len(found) > 10
    assert all(b.end - b.start <= MERGE_MAX_MS for b in found)
    # no black is lost: every one lies inside a boundary, and they stay in order
    assert all(any(f.start <= s and e <= f.end for f in found) for s, e in blacks)
    assert [f.start for f in found] == sorted(f.start for f in found)


def test_the_pair_black_card_black_is_still_merged_under_the_cap() -> None:
    blacks: list[Span] = [(10_000, 11_000), (16_900, 17_900)]  # the measured 5.9 s pattern
    assert black_boundaries(blacks, [], min_ms=500) == [Boundary(10_000, 17_900, False)]


def test_a_merged_boundary_is_silent_when_any_part_is() -> None:
    blacks: list[Span] = [(10_000, 11_000), (16_000, 17_000)]
    merged = black_boundaries(blacks, [(16_000, 17_500)], min_ms=500)
    assert merged == [Boundary(10_000, 17_000, True)]


def test_boundaries_come_back_in_time_order() -> None:
    blacks: list[Span] = [(60_000, 61_000), (1000, 3000), (30_000, 31_000)]
    starts = [b.start for b in black_boundaries(blacks, [], min_ms=500)]
    assert starts == [1000, 30_000, 60_000]


# --- theme cuts -------------------------------------------------------------------------------


def test_theme_starts_cut_the_compilation_into_episodes() -> None:
    spans = split_spans(105 * MS, theme_starts=[0, 35_000, 70_000], boundaries=[], s=S)
    assert spans == [(0, 35_000), (35_000, 70_000), (70_000, 105_000)]


def test_theme_at_the_start_is_an_intro_not_a_cut() -> None:
    # two interior themes: the first episode has no theme of its own
    spans = split_spans(100 * MS, theme_starts=[1500, 40_000, 70_000], boundaries=[], s=S)
    assert spans == [(0, 40_000), (40_000, 70_000), (70_000, 100_000)]


def test_two_interior_themes_make_a_compilation_even_when_short() -> None:
    s = CleanSettings(episode_min_s=10, compilation_min_s=1200)
    assert split_spans(50 * MS, theme_starts=[15_000, 32_000], boundaries=[], s=s) == [
        (0, 15_000),
        (15_000, 32_000),
        (32_000, 50_000),
    ]


def test_one_interior_theme_in_a_short_video_is_not_a_compilation() -> None:
    assert split_spans(50 * MS, theme_starts=[0, 25_000], boundaries=[], s=S) == []


def test_one_interior_theme_in_a_long_video_cuts_there() -> None:
    assert split_spans(80 * MS, theme_starts=[0, 40_000], boundaries=[], s=S) == [
        (0, 40_000),
        (40_000, 80_000),
    ]


def test_a_short_video_without_themes_is_whole() -> None:
    assert split_spans(40 * MS, theme_starts=[], boundaries=[_gap(20_000)], s=S) == []


def test_theme_pieces_shorter_than_episode_min_are_dropped() -> None:
    # the first 10 s are not an episode (20 s would be kept: the minimum is inclusive); the peaks
    # 20 s or more apart are all cuts
    spans = split_spans(100 * MS, theme_starts=[10_000, 30_000, 60_000], boundaries=[], s=S)
    assert spans == [(10_000, 30_000), (30_000, 60_000), (60_000, 100_000)]


def test_a_single_surviving_piece_is_not_a_split() -> None:
    assert split_spans(100 * MS, theme_starts=[10_000, 90_000], boundaries=[], s=S) == []


def test_a_theme_piece_within_the_maximum_is_not_cut_on_boundaries() -> None:
    boundaries = [_gap(24_500)]
    spans = split_spans(100 * MS, theme_starts=[0, 40_000, 70_000], boundaries=boundaries, s=S)
    assert spans == [(0, 40_000), (40_000, 70_000), (70_000, 100_000)]


def test_a_theme_piece_longer_than_the_maximum_is_cut_further_on_boundaries() -> None:
    # themes at 0 and 90 s: the pieces of 90 s and 70 s exceed 45 s, black boundaries cut them
    boundaries = [_gap(30_000), _gap(60_000), _gap(120_000), _gap(150_000)]
    spans = split_spans(160 * MS, theme_starts=[0, 90_000], boundaries=boundaries, s=S)
    assert spans == [
        (0, 29_500),
        (30_500, 59_500),
        (60_500, 90_000),  # the theme cut at 90 s is the peak itself: no gap
        (90_000, 119_500),
        (120_500, 160_000),
    ]


def test_a_theme_piece_with_no_boundary_to_cut_it_stays_whole() -> None:
    spans = split_spans(160 * MS, theme_starts=[0, 90_000], boundaries=[], s=S)
    assert spans == [(0, 90_000), (90_000, 160_000)]


def test_a_closing_generic_and_the_next_opening_are_one_cut_at_the_opening() -> None:
    # the same theme closes an episode (20 s) and opens the next (23 s): no piece between them
    starts = [0, 20_000, 23_000, 43_000, 46_000]
    spans = split_spans(66 * MS, theme_starts=starts, boundaries=[], s=S)
    assert spans == [(0, 23_000), (23_000, 46_000), (46_000, 66_000)]


def test_a_cold_open_between_a_closing_generic_and_the_opening_goes_with_the_closing() -> None:
    s = CleanSettings(episode_min_s=10, compilation_min_s=30)
    starts = [14_000, 23_000, 37_000, 46_000]  # closings at 14 s and 37 s, openings at 23 s, 46 s
    spans = split_spans(66 * MS, theme_starts=starts, boundaries=[], s=s)
    assert spans == [(0, 23_000), (23_000, 46_000), (46_000, 66_000)]  # children start at openings


def test_peaks_a_minimum_episode_apart_are_both_cuts() -> None:
    spans = split_spans(66 * MS, theme_starts=[20_000, 40_000], boundaries=[], s=S)
    assert spans == [(0, 20_000), (20_000, 40_000), (40_000, 66_000)]
    just_under = split_spans(66 * MS, theme_starts=[20_000, 39_999], boundaries=[], s=S)
    assert just_under == [(0, 39_999), (39_999, 66_000)]


def test_a_run_of_close_peaks_collapses_to_one_cut() -> None:
    spans = split_spans(80 * MS, theme_starts=[20_000, 25_000, 30_000], boundaries=[], s=S)
    assert spans == [(0, 30_000), (30_000, 80_000)]


def test_two_close_peaks_in_a_short_video_are_not_a_compilation() -> None:
    # closing + opening only: that is one cut, not two interior themes
    s = CleanSettings(episode_min_s=20, compilation_min_s=1200)
    assert split_spans(50 * MS, theme_starts=[20_000, 23_000], boundaries=[], s=s) == []


# --- black fallback ---------------------------------------------------------------------------


def test_fallback_cuts_around_the_black_and_leaves_it_out_of_both_episodes() -> None:
    boundaries = [_gap(30_000), _gap(62_000)]
    spans = split_spans(95 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 29_500), (30_500, 61_500), (62_500, 95_000)]


def test_fallback_needs_no_silence() -> None:
    boundaries = [_gap(30_000, silent=False), _gap(62_000, silent=False)]
    assert len(split_spans(95 * MS, theme_starts=[], boundaries=boundaries, s=S)) == 3


def test_fallback_takes_the_boundary_closest_to_the_typical_length() -> None:
    boundaries = [_gap(22_000), _gap(29_000), _gap(44_000)]
    spans = split_spans(100 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans[0] == (0, 28_500)


def test_fallback_ignores_a_black_inside_an_episode_far_from_the_typical_length() -> None:
    # a black at 44 s (a fade inside the episode) against the real boundary at 28 s
    boundaries = [_gap(28_000), _gap(44_000), _gap(58_500)]
    spans = split_spans(90 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 27_500), (28_500, 58_000), (59_000, 90_000)]


def test_fallback_tie_goes_to_the_silent_boundary_then_the_earliest() -> None:
    both = [Boundary(25_000, 25_500, False), Boundary(35_000, 35_500, False)]  # 5 s either side
    assert split_spans(100 * MS, theme_starts=[], boundaries=both, s=S)[0] == (0, 25_000)
    silent = [Boundary(25_000, 25_500, False), Boundary(35_000, 35_500, True)]
    assert split_spans(100 * MS, theme_starts=[], boundaries=silent, s=S)[0] == (0, 35_000)


def test_fallback_silence_never_beats_a_closer_black() -> None:
    boundaries = [Boundary(30_000, 30_500, False), Boundary(36_000, 36_500, True)]
    spans = split_spans(100 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans[0] == (0, 30_000)


def test_fallback_restarts_from_the_end_of_the_previous_black() -> None:
    # from 0 only 25 s fits [20, 45]; from 25.5 s the black at 60 s is 34 s away, 75 s is 49 s
    boundaries = [_gap(25_000), _gap(60_000), _gap(75_000)]
    spans = split_spans(100 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 24_500), (25_500, 59_500), (60_500, 100_000)]


def test_fallback_minimum_episode_length_is_inclusive() -> None:
    assert split_spans(100 * MS, theme_starts=[], boundaries=[Boundary(20_000, 20_500, False)], s=S)
    assert not split_spans(
        100 * MS, theme_starts=[], boundaries=[Boundary(19_999, 20_500, False)], s=S
    )


def test_fallback_maximum_episode_length_is_inclusive() -> None:
    spans = split_spans(
        100 * MS, theme_starts=[], boundaries=[Boundary(45_000, 45_500, False)], s=S
    )
    assert spans == [(0, 45_000), (45_500, 100_000)]


def test_a_remainder_longer_than_the_maximum_is_cut_at_a_black_beyond_it() -> None:
    # nothing within [20, 45] of the start, but 70 s of remainder is too long: cut at the black
    boundaries = [_gap(5000), _gap(70_000)]
    spans = split_spans(100 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 69_500), (70_500, 100_000)]


def test_the_tail_after_the_last_cut_is_checked_against_the_maximum() -> None:
    boundaries = [_gap(30_000), _gap(120_000)]
    spans = split_spans(150 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 29_500), (30_500, 119_500), (120_500, 150_000)]


def test_without_a_fitting_boundary_a_long_remainder_stays_whole() -> None:
    # blacks too close to an end to leave an episode of 20 s on either side
    assert split_spans(100 * MS, theme_starts=[], boundaries=[_gap(5000), _gap(95_000)], s=S) == []
    assert split_spans(100 * MS, theme_starts=[], boundaries=[], s=S) == []


def test_fallback_ignores_boundaries_outside_the_video() -> None:
    boundaries = [_gap(30_000), Boundary(99_800, 100_400, False), Boundary(120_000, 121_000, False)]
    spans = split_spans(100 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 29_500), (30_500, 100_000)]


def test_fallback_drops_a_short_tail() -> None:
    boundaries = [_gap(30_000), _gap(60_000)]
    spans = split_spans(70 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == [(0, 29_500), (30_500, 59_500)]  # the last 9.5 s are not an episode


def test_pieces_are_ordered_and_never_overlap() -> None:
    boundaries = [_gap(t) for t in (90_000, 31_000, 62_000, 124_000)]
    spans = split_spans(150 * MS, theme_starts=[], boundaries=boundaries, s=S)
    assert spans == sorted(spans)
    assert all(a[1] <= b[0] for a, b in pairwise(spans))
    assert all(end - start >= 20_000 for start, end in spans)


def test_a_run_of_blacks_never_hides_the_real_boundary_after_it() -> None:
    # blacks every 6 s from 400 s to 700 s, then a real boundary at 800 s (typical episode: 400 s)
    chain: list[Span] = [(t, t + 600) for t in range(400_000, 700_000, 6000)]
    real: list[Span] = [(800_000, 801_200)]
    boundaries = black_boundaries([*chain, *real], [], min_ms=500)
    s = CleanSettings(episode_min_s=240, compilation_min_s=1200, typical_episode_s=400)
    spans = split_spans(1_600_000, theme_starts=[], boundaries=boundaries, s=s)
    assert any(b == 800_000 for _, b in spans)  # an episode ends where the real black starts
    assert any(a == 801_200 for a, _ in spans)  # the next episode starts where the real black ends
    # whatever is cut out between two episodes is a boundary, never a long stretch of picture
    assert all(a[0] - b[1] <= MERGE_MAX_MS for b, a in pairwise(spans))
    assert sum(b[0] - a[1] for a, b in pairwise(spans)) < 3 * MERGE_MAX_MS


# --- Louis's real compilations (measured once, then kept as plain numbers) --------------------

# Real black and silence lists (ms), measured once on Louis's two compilations: plain numbers.
# Silences are those overlapping a black by 500 ms or more.
OGGY_MS = 4059870
OGGY_BLACKS: list[Span] = [
    (0, 1080),
    (40800, 42040),
    (47960, 49080),
    (440920, 442080),
    (448000, 448600),
    (540440, 541600),
    (634440, 635600),
    (792320, 793520),
    (840960, 842080),
    (848000, 848560),
    (982040, 982600),
    (1240800, 1242120),
    (1304080, 1305440),
    (1356040, 1357200),
    (1571520, 1572680),
    (1640840, 1642000),
    (2042800, 2044120),
    (2050040, 2051120),
    (2443000, 2444120),
    (2844960, 2845880),
    (2851800, 2852320),
    (3010280, 3011480),
    (3193000, 3194160),
    (3244640, 3245840),
    (3251760, 3252840),
    (3644720, 3645880),
    (3651720, 3652880),
    (3839120, 3840840),
    (4044640, 4045720),
]
OGGY_SILENCES: list[Span] = [
    (0, 1187),
    (41051, 42140),
    (792761, 793443),
    (841352, 842219),
    (1241084, 1242185),
    (1355968, 1357042),
    (1571512, 1572599),
    (1641307, 1642051),
    (2050222, 2051480),
    (3244941, 3245989),
]
TOM_JERRY_MS = 3168897
TOM_JERRY_BLACKS: list[Span] = [
    (0, 584),
    (5422, 6173),
    (24024, 25484),
    (260260, 261136),
    (551468, 552093),
    (656865, 657657),
    (834876, 838463),
    (938229, 939772),
    (1050341, 1051300),
    (1143851, 1145227),
    (1215548, 1217133),
    (1300007, 1300758),
    (1403027, 1404528),
    (1560893, 1561602),
    (2200323, 2201116),
    (2552967, 2553593),
    (3150022, 3150606),
    (3158238, 3158822),
]
TOM_JERRY_SILENCES: list[Span] = [(1050437, 1050982)]


def test_real_oggy_black_only_splits_into_its_ten_episodes() -> None:
    boundaries = black_boundaries(OGGY_BLACKS, OGGY_SILENCES, min_ms=500)
    spans = split_spans(OGGY_MS, theme_starts=[], boundaries=boundaries, s=CleanSettings())
    assert spans == [
        (0, 440_920),
        (448_600, 840_960),
        (848_560, 1_240_800),
        (1_242_120, 1_640_840),
        (1_642_000, 2_042_800),
        (2_051_120, 2_443_000),
        (2_444_120, 2_844_960),
        (2_852_320, 3_244_640),
        (3_252_840, 3_644_720),
        (3_652_880, 4_044_640),
    ]
    assert all(391_000 <= end - start <= 441_000 for start, end in spans)
    # every cut is a real boundary: a black, then (title card, second black), then the next episode
    for (_, end), (start, _) in pairwise(spans):
        assert 1000 <= start - end <= 9000


def test_real_oggy_black_and_silence_alone_would_cut_inside_an_episode() -> None:
    # what the first rule gave on this file: only blacks with a silence (793 s is a gag pause)
    silent_only = [b for b in black_boundaries(OGGY_BLACKS, OGGY_SILENCES, min_ms=500) if b.silent]
    assert any(b.start == 792_320 for b in silent_only)  # a pause inside episode 2...
    spans = split_spans(OGGY_MS, theme_starts=[], boundaries=silent_only, s=CleanSettings())
    assert not any(end == 440_920 for _, end in spans)  # ...and the real boundary at 441 s is lost


def test_real_tom_and_jerry_black_only_pieces_never_exceed_the_maximum() -> None:
    boundaries = black_boundaries(TOM_JERRY_BLACKS, TOM_JERRY_SILENCES, min_ms=500)
    spans = split_spans(TOM_JERRY_MS, theme_starts=[], boundaries=boundaries, s=CleanSettings())
    assert spans == [
        (0, 551_468),
        (552_093, 938_229),
        (939_772, 1_403_027),
        (1_404_528, 2_200_323),
        (2_201_116, 2_552_967),
        (2_553_593, 3_150_022),
    ]
    assert all(end - start <= 1_800_000 for start, end in spans)
