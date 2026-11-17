"""Source videos found by the discovery: queries, upserts, full-extraction details, signals."""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rushes.db.models import SourceVideo, VideoSignal
from rushes.discover.signals import comment_timestamps, heatmap_signals

WATCH_URL = "https://www.youtube.com/watch?v={id}"
QUERY_CHARACTERS = 2  # characters that add a query


def candidate_queries(title: str, characters: Sequence[str]) -> list[str]:
    """Keyword searches derived from the series: the title, then variants that surface episodes."""
    queries = [title, f"{title} cartoon", f"{title} full episode"]
    queries.extend(f"{title} {name.replace('_', ' ')}" for name in characters[:QUERY_CHARACTERS])
    return queries


def video_url(info: Mapping[str, Any]) -> str | None:
    """Canonical watch URL: from the id, else the entry's own URL."""
    video_id = info.get("id")
    if isinstance(video_id, str) and video_id and not video_id.startswith("UC"):
        return WATCH_URL.format(id=video_id)
    url = info.get("webpage_url") or info.get("url")
    return url if isinstance(url, str) and "watch?v=" in url else None


def published_date(info: Mapping[str, Any]) -> date | None:
    stamp = info.get("upload_date")
    if isinstance(stamp, str) and len(stamp) == 8 and stamp.isdigit():
        try:
            return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        except ValueError:
            return None
    seconds = info.get("timestamp")
    if isinstance(seconds, int | float):
        return datetime.fromtimestamp(seconds, UTC).date()
    return None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _fields(info: Mapping[str, Any]) -> dict[str, Any]:
    """The columns this answer knows about (a flat entry knows fewer than a full extraction)."""
    handle = info.get("uploader_id")
    duration = info.get("duration")
    published = published_date(info)
    fields: dict[str, Any] = {
        "title": info.get("title") if isinstance(info.get("title"), str) else None,
        "channel_id": info.get("channel_id") if isinstance(info.get("channel_id"), str) else None,
        "channel_handle": handle if isinstance(handle, str) and handle.startswith("@") else None,
        "views": _int(info.get("view_count")),
        "likes": _int(info.get("like_count")),
        "comments": _int(info.get("comment_count")),
        "duration_ms": round(duration * 1000) if isinstance(duration, int | float) else None,
        "published_at": published.isoformat() if published else None,
        "heatmap_json": (
            json.dumps(info["heatmap"], separators=(",", ":")) if info.get("heatmap") else None
        ),
    }
    return {name: value for name, value in fields.items() if value is not None}


def _owner(series_id: str | None, creator_id: str | None) -> tuple[str, str | None]:
    """The series when there is one, else the creator (C56)."""
    return ("series", series_id) if series_id is not None else ("creator", creator_id)


def upsert_source_video(
    session: Session,
    info: Mapping[str, Any],
    *,
    kind: Literal["official", "community"],
    series_id: str | None,
    creator_id: str | None,
    via: str,
    keep_kind: bool = False,
) -> int:
    """Insert or refresh a video by URL; returns its id.

    A known video only gains information: a value the new answer lacks is kept, the first way it
    was found stays. Its owner is its series, else its creator; it is set once (by the first
    reader of an ownerless row) and never changes, so another series or creator listing it never
    becomes a co-owner. Its `kind` follows the latest answer of its owner (a channel can be
    demoted, R15), never once it was downloaded, nor when `keep_kind` (the officials are unknown).
    """
    url = video_url(info)
    if url is None:
        raise ValueError(f"vidéo sans identifiant : {info.get('title')!r}")
    fields = _fields(info)
    video = session.scalar(select(SourceVideo).where(SourceVideo.url == url))
    if video is None:
        video = SourceVideo(
            platform="youtube",
            url=url,
            kind=kind,
            series_id=series_id,
            creator_id=creator_id,
            status="found",
            discovered_via=via,
            **fields,
        )
        session.add(video)
    else:
        for name, value in fields.items():
            setattr(video, name, value)
        ownerless = video.series_id is None and video.creator_id is None
        if ownerless:  # the first reader becomes the owner; an owner never changes
            video.series_id, video.creator_id = series_id, creator_id
        if (
            not keep_kind
            and video.status != "downloaded"
            and _owner(video.series_id, video.creator_id) == _owner(series_id, creator_id)
        ):
            video.kind = kind  # only the owner of a video decides its kind
        video.discovered_via = video.discovered_via or via
    session.flush()
    return video.id


def replace_signals(
    session: Session,
    source_video_id: int,
    kind: str,
    signals: Sequence[tuple[int, float]],
    *,
    at: datetime,
) -> None:
    """Signals are recomputed at every pass: the old ones of this kind go, the new ones come."""
    session.execute(
        delete(VideoSignal).where(
            VideoSignal.source_video_id == source_video_id, VideoSignal.kind == kind
        )
    )
    stamp = at.astimezone(UTC).isoformat(timespec="seconds")
    session.add_all(
        VideoSignal(
            source_video_id=source_video_id, kind=kind, t_ms=t_ms, weight=w, collected_at=stamp
        )
        for t_ms, w in signals
    )
    session.flush()


def store_full_extraction(
    session: Session, source_video_id: int, info: Mapping[str, Any], *, at: datetime
) -> None:
    """Details of a full extraction: counters, date, heatmap and comment signals, extracted_at."""
    video = session.get(SourceVideo, source_video_id)
    if video is None:
        raise ValueError(f"vidéo source inconnue : {source_video_id}")
    for name, value in _fields(info).items():
        setattr(video, name, value)
    if not info.get("heatmap"):
        video.heatmap_json = None  # the column follows the signals below
    video.extracted_at = at.astimezone(UTC).isoformat(timespec="seconds")
    replace_signals(
        session, source_video_id, "heatmap", heatmap_signals(info.get("heatmap")), at=at
    )
    comments = info.get("comments")
    if comments:
        replace_signals(session, source_video_id, "comment_ts", comment_timestamps(comments), at=at)
    session.flush()
