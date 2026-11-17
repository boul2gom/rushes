"""Score of a source video and strong moments (spec, Algorithmes)."""

import json
from datetime import date
from math import log1p
from pathlib import Path

import pytest

from rushes.config import DiscoverWeights
from rushes.discover.score import VideoStats, video_score
from rushes.discover.signals import comment_timestamps, heatmap_signals

D = date(2026, 11, 16)
W = DiscoverWeights()  # 0.5 views per day, 0.3 overperformance, 0.2 engagement

# Worked by hand: a = log10(1 + views/days), b = log10(1 + views/median),
# c = log10(1 + 1000 (likes + 2 comments) / views); mean of the available parts.
SCORE_CASES = [
    # all three: a = log10(10001) = 4.0000434, b = log10(5) = 0.6989700, c = log10(61) = 1.7853298
    #   0.5 a + 0.3 b + 0.2 c = 2.5667787
    (VideoStats(1_000_000, 50_000, 5_000, date(2026, 8, 8), 250_000), 2.5667787),
    # likes hidden: c is missing, the two weights left (0.5, 0.3) are renormalized
    #   a = log10(20001) = 4.3010517, b = log10(3) = 0.4771213 ; (0.5 a + 0.3 b) / 0.8 = 2.8670778
    (VideoStats(200_000, None, None, date(2026, 11, 6), 100_000), 2.8670778),
    # no date, no median, comments disabled (0): only c = log10(1 + 1000 x 100 / 5000) = log10(21)
    (VideoStats(5_000, 100, None, None, None), 1.3222193),
]


@pytest.mark.parametrize(("stats", "expected"), SCORE_CASES)
def test_video_score_reference_values(stats: VideoStats, expected: float) -> None:
    assert video_score(stats, today=D, w=W) == pytest.approx(expected)


def test_video_score_without_any_component_is_zero() -> None:
    assert video_score(VideoStats(None, None, None, None, None), today=D, w=W) == 0.0
    assert video_score(VideoStats(None, 10, 3, D, 5.0), today=D, w=W) == 0.0


def test_video_score_published_today_counts_one_day_and_zero_views_are_safe() -> None:
    today = video_score(VideoStats(1_000, None, None, D, None), today=D, w=W)
    assert today == pytest.approx(3.0004341)  # log10(1 + 1000 / 1)
    assert video_score(VideoStats(0, 5, 0, D, 100.0), today=D, w=W) == 0.0  # a = b = 0, no c


def test_video_score_follows_the_weights() -> None:
    stats = VideoStats(1_000_000, 50_000, 5_000, date(2026, 8, 8), 250_000)
    only_engagement = DiscoverWeights(views_per_day=0, overperformance=0, engagement=1)
    assert video_score(stats, today=D, w=only_engagement) == pytest.approx(1.7853298)
    nothing = DiscoverWeights(views_per_day=0, overperformance=0, engagement=0)
    assert video_score(stats, today=D, w=nothing) == 0.0


def test_heatmap_and_comment_signals_parsed() -> None:
    assert heatmap_signals(None) == [] and heatmap_signals([]) == []
    assert heatmap_signals(
        [
            {"start_time": 0.0, "end_time": 12.45, "value": 0.65},
            {"start_time": 12.45, "end_time": 24.9, "value": 0.15},
            {"start_time": 30.0},  # incomplete: ignored
        ]
    ) == [(6225, 0.65), (18675, 0.15)]

    comments = [
        {"text": "1:23 is the best, 12:05 too, and 1:02:03 at the end", "like_count": 10},
        {"text": "nothing to see", "like_count": 3},
        {"text": "at 1:75 or 99:99:99 no", "like_count": 0},
        {"text": "0:07", "like_count": None},
    ]
    weight = 1 + log1p(10)
    assert comment_timestamps(comments) == [
        (83_000, weight),
        (725_000, weight),
        (3_723_000, weight),
        (7_000, 1.0),
    ]


def test_heatmap_of_a_recorded_video_is_parsed() -> None:
    folder = Path(__file__).parent / "fixtures" / "cassettes"
    heatmaps = [
        info["heatmap"]
        for path in folder.glob("*.json")
        if "heatmap" in (info := json.loads(path.read_text(encoding="utf-8"))["info"])
    ]
    assert len(heatmaps) == 1  # the recorded video that has one
    signals = heatmap_signals(heatmaps[0])
    assert len(signals) == len(heatmaps[0]) == 100
    assert signals[0][0] == 6225 and signals == sorted(signals)
