"""Which discovered YouTube videos to download (task 10b): the stock decides, not a fixed quota.

A series needs posts when the stock of the accounts that may own its episodes is under
`stock_days_target`. Official videos fill the largest need first; community videos are signals,
at most one per series and per run, and never more than `community_max_per_series` in all.
"""

from collections import deque
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rushes.config import Config, IngestSettings
from rushes.db.models import Episode, Series, SourceVideo
from rushes.errors import RushesError
from rushes.ingest.owner import candidate_accounts
from rushes.ingest.stock import account_posts_per_day, stock_days

__all__ = ["select_downloads", "stock_days"]

# `retained` videos first, then the `failed` ones (a failed download is tried again at the next
# run, but a dead video must not block the good ones behind it).
_DOWNLOADABLE = ("retained", "failed")


def _need(session: Session, config: Config, s: IngestSettings, accounts: list[str]) -> float:
    """Posts the accounts lack to reach the target stock."""
    total = 0.0
    for account in accounts:
        stock = stock_days(session, config, account, est_post_s=s.est_post_s)
        total += max(0.0, s.stock_days_target - stock) * account_posts_per_day(config, account)
    return total


def _queue(session: Session, series_id: str, kind: str) -> deque[SourceVideo]:
    videos = session.scalars(
        select(SourceVideo).where(
            SourceVideo.series_id == series_id,
            SourceVideo.kind == kind,
            SourceVideo.platform == "youtube",
            SourceVideo.status.in_(_DOWNLOADABLE),
            SourceVideo.duration_ms.is_not(None),
            SourceVideo.duration_ms > 0,
        )
    )
    ordered = sorted(
        videos,
        key=lambda v: (v.status != "retained", -(v.score if v.score is not None else -1.0), v.id),
    )
    return deque(ordered)


def select_downloads(
    session: Session,
    config: Config,
    s: IngestSettings,
    *,
    today: date,
    series: str | None = None,
    creator: str | None = None,
) -> list[int]:
    """Ids of the source videos to download in this run, in download order.

    `series` / `creator` limit the subjects (a discovery belongs to a series: a creator filter
    selects nothing). A series that no account may own (R4) is never downloaded.
    """
    if creator is not None or s.max_downloads_per_run <= 0:
        return []
    ids = session.scalars(
        select(Series.id)
        .where(Series.status == "active", Series.origin == "config")
        .order_by(Series.id)
    ).all()
    need: dict[str, float] = {}
    for series_id in ids:
        if (series is not None and series_id != series) or config.series_by_id(series_id) is None:
            continue
        try:
            accounts = candidate_accounts(config, series_id=series_id, creator_id=None, today=today)
        except RushesError:
            continue  # no account for it in the current rotation: nothing to download
        need[series_id] = _need(session, config, s, accounts)

    picked: list[int] = []
    queues = {series_id: _queue(session, series_id, "official") for series_id in need}
    remaining = dict(need)
    while len(picked) < s.max_downloads_per_run:
        open_ = [sid for sid, queue in queues.items() if queue and remaining[sid] > 0]
        if not open_:
            break
        sid = min(open_, key=lambda x: (-remaining[x], x))
        video = queues[sid].popleft()
        picked.append(video.id)
        assert video.duration_ms is not None
        remaining[sid] -= video.duration_ms / 1000 / s.est_post_s

    for sid in sorted(need, key=lambda x: (-need[x], x)):
        if len(picked) >= s.max_downloads_per_run:
            break
        # a split video leaves a parent and children with one source_video_id: it counts once
        held = session.scalar(
            select(func.count(func.distinct(Episode.source_video_id))).where(
                Episode.series_id == sid, Episode.provenance == "community"
            )
        )
        community = _queue(session, sid, "community")
        if (held or 0) < s.community_max_per_series and community:
            picked.append(community[0].id)
    return picked
