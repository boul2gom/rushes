"""Strong moments read from YouTube metadata, in the time of the SOURCE video."""

import re
from collections.abc import Mapping, Sequence
from math import log1p
from typing import Any

_TIMESTAMP = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")


def heatmap_signals(heatmap: Sequence[Mapping[str, float]] | None) -> list[tuple[int, float]]:
    """`(t_ms of the middle of each segment, value)`; nothing when the video has no heatmap."""
    signals: list[tuple[int, float]] = []
    for segment in heatmap or []:
        try:
            middle_s = (float(segment["start_time"]) + float(segment["end_time"])) / 2
            value = float(segment["value"])
        except KeyError, TypeError, ValueError:
            continue
        signals.append((round(middle_s * 1000), value))
    return signals


def comment_timestamps(comments: Sequence[Mapping[str, Any]]) -> list[tuple[int, float]]:
    """`(t_ms, weight)` for each `m:ss` or `h:mm:ss` in a comment; weight 1 + log1p(likes)."""
    signals: list[tuple[int, float]] = []
    for comment in comments:
        weight = 1 + log1p(max(comment.get("like_count") or 0, 0))
        for hours, minutes, seconds in _TIMESTAMP.findall(str(comment.get("text") or "")):
            h, m, s = int(hours or 0), int(minutes), int(seconds)
            if s >= 60 or (hours and m >= 60):
                continue  # "1:75" is not a time
            signals.append((((h * 60 + m) * 60 + s) * 1000, weight))
    return signals
