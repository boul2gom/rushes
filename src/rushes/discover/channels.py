"""Finding a series' official YouTube channels from the channel search (spec, Sources).

One channel search gives the verified badge and the follower count; what a channel publishes
(its most viewed titles) tells the real channel from a look-alike. Doubt keeps a channel
`community` (R15) until Louis confirms it in the config.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote_plus

from rapidfuzz import fuzz

from rushes.config import TIKTOK_HANDLE, DiscoverSettings
from rushes.core.text import fold_text

CONTENT_MATCH = 80  # partial_ratio from which a video title "is about" the series
COMMUNITY_MIN_SHARE = 0.5
POPULAR_TITLES = 30

_TIKTOK_PATTERNS = (
    re.compile(r"tiktok\.com/@([\w.]+)", re.IGNORECASE),
    re.compile(r"tiktok\s*[:\uff1a]\s*@([\w.]+)", re.IGNORECASE),  # ASCII or fullwidth colon
)


@dataclass(frozen=True)
class ChannelCandidate:
    channel_id: str
    handle: str | None
    title: str
    verified: bool
    followers: int | None
    name_sim: float  # 0-100, series title against channel title
    content_share: float  # 0-1, share of the popular titles that are about the series


def channel_search_url(query: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=EgIQAg%3D%3D"


def popular_playlist_url(channel_id: str) -> str:
    """The "popular videos" playlist: UULP + the channel id without its "UC"."""
    return f"https://www.youtube.com/playlist?list=UULP{channel_id[2:]}"


def channel_videos_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}/videos"


def _content_share(series: str, titles: Sequence[str]) -> float:
    sample = titles[:POPULAR_TITLES]
    if not sample:
        return 0.0
    about = sum(fuzz.partial_ratio(series, fold_text(title)) >= CONTENT_MATCH for title in sample)
    return about / len(sample)


def rank_channels(
    series_title: str,
    search_info: Mapping[str, Any],
    popular_titles: Mapping[str, Sequence[str]],
) -> list[ChannelCandidate]:
    """Channels of a channel search, in search order, with their similarity scores.

    Entries that are not channels (shelves of videos) are skipped; a channel whose popular
    titles were not read has a `content_share` of 0.
    """
    series = fold_text(series_title)
    seen: set[str] = set()
    candidates: list[ChannelCandidate] = []
    for entry in search_info.get("entries") or []:
        channel_id = str(entry.get("channel_id") or entry.get("id") or "")
        if not channel_id.startswith("UC") or channel_id in seen:
            continue
        seen.add(channel_id)
        title = str(entry.get("channel") or entry.get("title") or "")
        handle = entry.get("uploader_id")
        followers = entry.get("channel_follower_count")
        candidates.append(
            ChannelCandidate(
                channel_id=channel_id,
                handle=handle if isinstance(handle, str) and handle.startswith("@") else None,
                title=title,
                verified=bool(entry.get("channel_is_verified")),
                followers=followers if isinstance(followers, int) else None,
                name_sim=float(fuzz.token_set_ratio(series, fold_text(title))),
                content_share=_content_share(series, popular_titles.get(channel_id, ())),
            )
        )
    return candidates


def classify(
    c: ChannelCandidate, s: DiscoverSettings
) -> tuple[Literal["official", "community", "ignore"], float]:
    """`(kind, confidence)`; doubt keeps a channel `community` (R15).

    Official needs the badge, the audience, content about the series (Louis, 2026-11-17: a name
    alone is not enough) and a matching name or plenty of such content.
    """
    about_the_series = c.content_share >= COMMUNITY_MIN_SHARE
    if (
        c.verified
        and (c.followers or 0) >= s.official_min_followers
        and about_the_series
        and (
            c.name_sim >= s.official_min_name_sim or c.content_share >= s.official_min_content_share
        )
    ):
        return "official", min(1.0, 0.4 + 0.3 * c.name_sim / 100 + 0.3 * c.content_share)
    if about_the_series:
        return "community", 0.3 + 0.4 * c.content_share
    return "ignore", 0.0


def tiktok_handles(descriptions: Iterable[str]) -> list[str]:
    """Official TikTok accounts quoted in video descriptions: `@name`, lowercase, no duplicate."""
    handles: list[str] = []
    for description in descriptions:
        for pattern in _TIKTOK_PATTERNS:
            for name in pattern.findall(description):
                handle = f"@{name.rstrip('.').lower()}"
                if TIKTOK_HANDLE.fullmatch(handle) and handle not in handles:
                    handles.append(handle)
    return handles
