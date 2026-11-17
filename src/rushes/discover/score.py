"""Score of a source video (spec, Algorithmes): a weighted mean of the components we can compute."""

from dataclasses import dataclass
from datetime import date
from math import log10

from rushes.config import DiscoverWeights


@dataclass(frozen=True)
class VideoStats:
    views: int | None
    likes: int | None  # None: hidden by the channel
    comments: int | None  # None: disabled (counts as 0 once likes are known)
    published: date | None
    channel_median_views: float | None  # median of the channel's latest videos


def video_score(v: VideoStats, *, today: date, w: DiscoverWeights) -> float:
    """Mean of the available components, weights renormalized; no component: 0.

    a = log10(1 + views per day), b = log10(1 + views / channel median),
    c = log10(1 + 1000 x (likes + 2 x comments) / views).
    """
    parts: list[tuple[float, float]] = []  # (weight, value)
    if v.views is not None and v.published is not None:
        days = max((today - v.published).days, 1)
        parts.append((w.views_per_day, log10(1 + v.views / days)))
    if v.views is not None and v.channel_median_views:
        parts.append((w.overperformance, log10(1 + v.views / v.channel_median_views)))
    if v.views and v.likes is not None:
        engagement = 1000 * (v.likes + 2 * (v.comments or 0)) / v.views
        parts.append((w.engagement, log10(1 + engagement)))
    total = sum(weight for weight, _ in parts)
    if total <= 0:
        return 0.0
    return sum(weight * value for weight, value in parts) / total
