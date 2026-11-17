"""`discover youtube`: official channels, most viewed videos, score and strong moments of a series.

One item per active series. Requests go through `ctx.net.ytdlp` (metadata only, R18), each phase
commits on its own and no transaction stays open during a request. A failing request makes the
series `warned` and keeps what was already stored; YouTube asking for a login stops the series.
"""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from rushes.config import DiscoverSettings, SeriesConfig
from rushes.context import Context, Filters
from rushes.db.derived import Resolved, record, resolve
from rushes.db.models import Series, SourceVideo
from rushes.discover.channels import (
    ChannelCandidate,
    channel_search_url,
    channel_videos_url,
    classify,
    popular_playlist_url,
    rank_channels,
    tiktok_handles,
)
from rushes.discover.score import VideoStats, video_score
from rushes.discover.videos import (
    candidate_queries,
    store_full_extraction,
    upsert_source_video,
)
from rushes.net.ytdlp import ExtractError
from rushes.runner import BaseStep, Outcome

OFFICIAL_CHANNELS = "youtube.official_channels"
COMMUNITY_CHANNELS = "youtube.community_channels"
SEARCH_QUERIES = "youtube.search_queries"
LAST_DISCOVERY = "youtube.last_discovery"
TIKTOK_ACCOUNTS = "tiktok.official_accounts"

SEARCH_CHANNELS = 10  # channels read from the channel search
CANDIDATES_CHECKED = 5  # channels whose popular titles are read
CANDIDATE_TITLES = 30  # titles read per candidate, and per community channel
KEPT_QUERIES = 3  # best keyword searches kept as the derived value
MAX_FAILURES = 3  # consecutive failed requests after which the source is taken as unavailable
RESOLVABLE = ("found", "retained")  # statuses this step owns (downloaded and failed are 10b's)


def _ids(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("liste de textes attendue")
    return list(value)


def _day(value: object) -> date | None:
    return date.fromisoformat(str(value))


def _old(resolved: Resolved[Any], now: datetime, days: int) -> bool:
    """Calendar days, like the selection of the series (a run at 03:00:05 is not "too early")."""
    return resolved.computed_at is None or (now.date() - resolved.computed_at.date()).days >= days


def _median_views(entries: Iterable[Mapping[str, Any]]) -> float:
    views = [e["view_count"] for e in entries if isinstance(e.get("view_count"), int | float)]
    return float(statistics.median(views)) if views else 0.0


def _entries(info: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [e for e in info.get("entries") or [] if isinstance(e, dict)]


@dataclass
class _Tally:
    found: int = 0
    extracted: int = 0
    retained: int = 0
    errors: list[str] = field(default_factory=list)


class _SeriesRun:
    """The state of one series' discovery: what was asked, what failed, what is known."""

    def __init__(self, ctx: Context, series: SeriesConfig, *, force: bool) -> None:
        self.ctx = ctx
        self.series = series
        self.force = force  # redo the extraction of the most viewed videos, even the extracted ones
        self.settings: DiscoverSettings = ctx.config.settings.discover
        self.now = ctx.clock.now()
        self.today = self.now.date()
        self.tally = _Tally()
        self.blocked = False  # YouTube asked for a login or keeps failing: no more requests
        self.failures = 0  # consecutive failed requests
        self.keep_kinds = False  # the channel search failed: no stored kind changes this run
        self.unchecked: list[str] = []  # candidates unread on a first search: told, retried
        self.memo: dict[tuple[str, str | None, bool], dict[str, Any]] = {}
        self.medians: dict[str, float] = {}
        self.seen: set[int] = set()  # videos listed during this run
        self.officials: list[str] = []

    # --- requests ------------------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        playlist_items: str | None = None,
        *,
        flat: bool = True,
        optional: bool = False,
    ) -> dict[str, Any] | None:
        """One request (cookies only as R18 allows); None after a failure.

        A failure is noted for the series, unless the request is `optional` and YouTube did not ask
        for a login.
        """
        key = (url, playlist_items, flat)
        if key in self.memo:
            return self.memo[key]
        if self.blocked:
            return None
        try:
            info = self.ctx.net.ytdlp.extract(
                url,
                flat=flat,
                platform="youtube",
                playlist_items=playlist_items,
                cookies_allowed=True,  # only this step may (R18)
            )
        except ExtractError as exc:
            if not optional or exc.signin:  # a missing optional playlist says nothing of the source
                self.failures += 1
            self.blocked = exc.signin or self.failures >= MAX_FAILURES
            if exc.signin or not optional:
                self.tally.errors.append(str(exc) or "extraction impossible")
            return None
        self.failures = 0
        self.memo[key] = info
        return info

    # --- step 1: the channels ------------------------------------------------------------------

    def resolve_channels(self) -> tuple[list[str], list[str]]:
        """`(official, community)` channel ids: the config's, else the derived ones (refreshed)."""
        sources = self.series.sources.youtube_channels
        config_official = [c.id for c in sources if c.kind == "official"] or None
        config_community = [c.id for c in sources if c.kind == "community"] or None
        sid = self.series.id
        with Session(self.ctx.engine) as session:
            official = resolve(
                session,
                OFFICIAL_CHANNELS,
                sid,
                config_value=config_official,
                default=[],
                parse=_ids,
            )
            # no discovery completed yet (a first search left a candidate unread): search again
            never_done = (
                resolve(
                    session, LAST_DISCOVERY, sid, config_value=None, default=None, parse=_day
                ).value
                is None
            )
        if official.origin == "default" or (
            official.origin == "derived"
            and (never_done or _old(official, self.now, self.settings.refresh_days))
        ):
            self._search_channels()
        with Session(self.ctx.engine) as session:
            official = resolve(
                session,
                OFFICIAL_CHANNELS,
                sid,
                config_value=config_official,
                default=[],
                parse=_ids,
            )
            community = resolve(
                session,
                COMMUNITY_CHANNELS,
                sid,
                config_value=config_community,
                default=[],
                parse=_ids,
            )
        # the config wins over a derived value: a channel it calls community is never official (R15)
        demoted = {c.id for c in sources if c.kind == "community"}
        officials = [c for c in official.value if c not in demoted]
        return officials, [c for c in community.value if c not in officials]

    def sync_kinds(self, official: Sequence[str], community: Sequence[str]) -> None:
        """Stored videos of the series follow the resolved lists; `downloaded` ones are left alone.

        An official video whose channel is not in the official list (removed from the config, no
        longer classified official) becomes `community` (R15), unless the channel search failed:
        then the official list says nothing, and no stored kind changes this run. An optional
        failure (a candidate's popular titles) stops nothing: the carry-over covers it.
        """
        self.keep_kinds = bool(self.tally.errors)  # decided once, here, for the whole run
        with Session(self.ctx.engine) as session:
            for channel_ids, kind in ((official, "official"), (community, "community")):
                if channel_ids:
                    session.execute(
                        update(SourceVideo)
                        .where(
                            SourceVideo.series_id == self.series.id,
                            SourceVideo.channel_id.in_(channel_ids),
                            SourceVideo.kind != kind,
                            SourceVideo.status != "downloaded",
                        )
                        .values(kind=kind)
                    )
            if not self.keep_kinds:
                session.execute(
                    update(SourceVideo)
                    .where(
                        SourceVideo.series_id == self.series.id,
                        SourceVideo.kind == "official",
                        SourceVideo.status != "downloaded",
                        or_(
                            SourceVideo.channel_id.is_(None),
                            SourceVideo.channel_id.not_in(list(official)),
                        ),
                    )
                    .values(kind="community")
                )
            session.commit()

    def _search_channels(self) -> None:
        """Channel search, then the popular titles of the first candidates: classify and record."""
        info = self.fetch(channel_search_url(self.series.title), f"1:{SEARCH_CHANNELS}", flat=True)
        if info is None:
            return
        ranked = rank_channels(self.series.title, info, {})
        titles: dict[str, list[str]] = {}
        failed: set[str] = set()  # candidates whose popular titles could not be read
        for candidate in ranked[:CANDIDATES_CHECKED]:
            popular = self.fetch(
                popular_playlist_url(candidate.channel_id), f"1:{CANDIDATE_TITLES}", optional=True
            )
            if popular is None:
                if self.blocked:
                    return
                # no playlist or a timeout (the error does not tell): only the previous list
                # matters below, a candidate in it is carried over, any other is not recorded
                failed.add(candidate.channel_id)
                continue
            titles[candidate.channel_id] = [str(e.get("title") or "") for e in _entries(popular)]
        by_kind: dict[str, list[tuple[ChannelCandidate, float]]] = {"official": [], "community": []}
        for candidate in rank_channels(self.series.title, info, titles):
            kind, confidence = classify(candidate, self.settings)
            if kind != "ignore" and candidate.channel_id in titles:
                by_kind[kind].append((candidate, confidence))
        with Session(self.ctx.engine) as session:
            for key, kind in ((OFFICIAL_CHANNELS, "official"), (COMMUNITY_CHANNELS, "community")):
                found = by_kind[kind]
                kept = {c.channel_id for c, _ in found}
                confidence = max((conf for _, conf in found), default=0.0)
                # a candidate we could not read keeps its place in the previous derived list
                # (a timeout must not drop an official channel); the list stays in search order.
                # Known limit: a listed channel whose playlist is gone for good stays listed, and
                # every run warns (its official read fails) until Louis sets the config (C56).
                before = resolve(
                    session, key, self.series.id, config_value=None, default=list[str](), parse=_ids
                )
                carried = {c for c in before.value if c in failed}
                if carried and before.confidence is not None:
                    confidence = max(confidence, before.confidence)
                if kind == "official" and before.origin != "derived":
                    self.unchecked = [c.channel_id for c in ranked if c.channel_id in failed]
                record(
                    session,
                    key,
                    self.series.id,
                    [c.channel_id for c in ranked if c.channel_id in kept | carried],
                    source="channel_search",
                    confidence=confidence,
                    at=self.now,
                )
            session.commit()

    # --- steps 2 and 2 bis: videos of the channels ---------------------------------------------

    def _store_listing(
        self,
        entries: Sequence[dict[str, Any]],
        channel_id: str | None,
        *,
        kind: Literal["official", "community"],
        via: str,
        keep_kind: bool = False,
    ) -> None:
        with Session(self.ctx.engine) as session:
            for entry in entries:
                entry = {**entry, "channel_id": entry.get("channel_id") or channel_id}
                try:
                    video_id = upsert_source_video(
                        session,
                        entry,
                        kind=kind,
                        series_id=self.series.id,
                        creator_id=None,
                        via=via,
                        keep_kind=keep_kind,
                    )
                except ValueError:
                    continue  # a shelf or a deleted video without an address
                self.seen.add(video_id)
                self.tally.found += 1
            session.commit()

    def read_official(self, channel_id: str) -> None:
        popular = self.fetch(popular_playlist_url(channel_id), f"1:{self.settings.popular_limit}")
        if popular is not None:
            self._store_listing(_entries(popular), channel_id, kind="official", via="popular")
        recent = self.fetch(channel_videos_url(channel_id), f"1:{self.settings.median_window}")
        if recent is not None and _entries(recent):
            self.medians[channel_id] = _median_views(_entries(recent))

    def read_community(self, channel_id: str) -> None:
        listing = self.fetch(popular_playlist_url(channel_id), f"1:{CANDIDATE_TITLES}")
        if listing is not None:
            self._store_listing(_entries(listing), channel_id, kind="community", via="channel")

    # --- step 3: keyword searches --------------------------------------------------------------

    def search(self) -> None:
        sid = self.series.id
        config_queries = self.series.sources.search_queries or None
        defaults = candidate_queries(self.series.title, [c.id for c in self.series.characters])
        with Session(self.ctx.engine) as session:
            queries = resolve(
                session,
                SEARCH_QUERIES,
                sid,
                config_value=config_queries,
                default=defaults,
                parse=_ids,
            )
        refresh = queries.origin == "default" or (
            queries.origin == "derived" and _old(queries, self.now, self.settings.refresh_days)
        )
        to_run = defaults if refresh else queries.value
        medians: list[tuple[float, str]] = []
        for query in to_run:
            listing = self.fetch(f"ytsearch{self.settings.search_results}:{query}")
            if listing is None:
                if self.blocked:
                    return
                continue
            entries = _entries(listing)
            medians.append((_median_views(entries), query))
            others = [e for e in entries if e.get("channel_id") not in self.officials]
            # the channel search failed: the officials are unknown, a listed video keeps its kind
            self._store_listing(
                others, None, kind="community", via="search", keep_kind=self.keep_kinds
            )
        if refresh and medians:
            best = [q for _, q in sorted(medians, key=lambda m: -m[0])[:KEPT_QUERIES]]
            with Session(self.ctx.engine) as session:
                record(
                    session,
                    SEARCH_QUERIES,
                    sid,
                    best,
                    source="query_views",
                    confidence=0.6,
                    at=self.now,
                )
                session.commit()

    # --- step 4: duration ----------------------------------------------------------------------

    def filter_durations(self) -> None:
        low, high = self.settings.min_duration_s * 1000, self.settings.max_duration_s * 1000
        with Session(self.ctx.engine) as session:
            videos = session.scalars(
                select(SourceVideo).where(
                    SourceVideo.series_id == self.series.id,
                    SourceVideo.status.in_(RESOLVABLE),
                    SourceVideo.duration_ms.is_not(None),
                )
            )
            for video in videos:
                # `rejected` is shared with other steps and carries no reason: never lifted here
                if not low <= (video.duration_ms or 0) <= high:
                    video.status = "rejected"
            session.commit()

    # --- step 5: full extractions --------------------------------------------------------------

    def extract_details(self) -> None:
        """Likes, date, description, heatmap of the most viewed videos never extracted.

        `--force` takes the most viewed ones again, extracted or not. Only the videos this series
        owns: another owner's video (and its kind) never feeds this series' TikTok accounts.
        """
        query = select(SourceVideo.id, SourceVideo.url, SourceVideo.kind).where(
            SourceVideo.id.in_(self.seen),
            SourceVideo.series_id == self.series.id,
            SourceVideo.status.in_(RESOLVABLE),
        )
        if not self.force:
            query = query.where(SourceVideo.extracted_at.is_(None))
        with Session(self.ctx.engine) as session:
            todo = session.execute(
                query.order_by(SourceVideo.views.desc(), SourceVideo.id).limit(
                    self.settings.full_extract_per_run
                )
            ).all()
        descriptions: list[str] = []
        for video_id, url, kind in todo:
            info = self.fetch(url, flat=False)
            if info is None:
                if self.blocked:
                    break
                continue
            with Session(self.ctx.engine) as session:
                store_full_extraction(session, video_id, info, at=self.now)
                session.commit()
            self.tally.extracted += 1
            if kind == "official" and isinstance(info.get("description"), str):
                descriptions.append(info["description"])
        handles = tiktok_handles(descriptions)
        if handles:
            with Session(self.ctx.engine) as session:
                known: list[str] = resolve(
                    session,
                    TIKTOK_ACCOUNTS,
                    self.series.id,
                    config_value=None,
                    default=list[str](),
                    parse=_ids,
                ).value
                record(
                    session,
                    TIKTOK_ACCOUNTS,
                    self.series.id,
                    [*known, *(h for h in handles if h not in known)],
                    source="video_descriptions",
                    confidence=0.6,
                    at=self.now,
                )
                session.commit()

    # --- step 6: scores and selection ----------------------------------------------------------

    def score_and_retain(self) -> None:
        w = self.settings.weights
        with Session(self.ctx.engine) as session:
            videos = list(
                session.scalars(
                    select(SourceVideo).where(
                        SourceVideo.series_id == self.series.id,
                        SourceVideo.extracted_at.is_not(None),
                        SourceVideo.status.in_(RESOLVABLE),
                    )
                )
            )
            for row in videos:
                published = date.fromisoformat(row.published_at) if row.published_at else None
                row.score = video_score(
                    VideoStats(
                        row.views,
                        row.likes,
                        row.comments,
                        published,
                        self.medians.get(row.channel_id or ""),
                    ),
                    today=self.today,
                    w=w,
                )
            ranked = sorted(videos, key=lambda v: (-(v.score or 0.0), v.url))
            official = [
                v
                for v in ranked
                if v.kind == "official" and (v.score or 0.0) >= self.settings.min_score
            ]
            community = [v for v in ranked if v.kind == "community"]
            chosen = {
                v.id
                for v in (
                    *official[: self.settings.keep_top],
                    *community[: self.settings.keep_community],
                )
            }
            for row in videos:
                row.status = "retained" if row.id in chosen else "found"
            session.commit()
            self.tally.retained = len(chosen)

    # --- whole series --------------------------------------------------------------------------

    def run(self) -> Outcome:
        official, community = self.resolve_channels()
        self.officials = official
        self.sync_kinds(official, community)
        for channel_id in official:
            self.read_official(channel_id)
        for channel_id in community:
            self.read_community(channel_id)
        self.search()
        self.filter_durations()
        self.extract_details()
        self.filter_durations()  # again: a flat entry may have lacked its duration
        self.score_and_retain()
        t = self.tally
        summary = f"{t.found} vidéos listées, {t.extracted} extraites, {t.retained} retenues"
        if t.errors:
            more = f" (+{len(t.errors) - 1})" if len(t.errors) > 1 else ""
            return Outcome(
                "warned",
                f"{summary} ; source indisponible : {t.errors[0]}{more}",
                source_unavailable=True,
            )
        if self.unchecked:  # optional: not a lost source, but Louis is told and it is retried
            return Outcome(
                "warned",
                f"{summary} ; {len(self.unchecked)} chaîne(s) candidate(s) non vérifiée(s) "
                "à la première recherche, nouvel essai au prochain passage",
            )
        with Session(self.ctx.engine) as session:
            record(
                session,
                LAST_DISCOVERY,
                self.series.id,
                self.today.isoformat(),
                source="discover_youtube",
                confidence=1.0,
                at=self.now,
            )
            session.commit()
        return Outcome("done", summary)


class DiscoverYoutubeStep(BaseStep):
    name: ClassVar[str] = "discover_youtube"  # items: the active series to refresh

    def key(self, item: str) -> str:
        return item

    def select(self, ctx: Context, filters: Filters) -> Iterable[str]:
        if filters.creator or filters.episode is not None or filters.account:
            return []  # a discovery belongs to a series
        today = ctx.clock.now().date()
        refresh = timedelta(days=ctx.config.settings.discover.refresh_days)
        with Session(ctx.engine) as session:
            ids = session.scalars(
                select(Series.id).where(Series.status == "active").order_by(Series.id)
            ).all()
            wanted = [
                sid
                for sid in ids
                if ctx.config.series_by_id(sid) is not None
                and (filters.series is None or sid == filters.series)
            ]
            if filters.force:
                return wanted
            due: list[str] = []
            for sid in wanted:
                last = resolve(
                    session, LAST_DISCOVERY, sid, config_value=None, default=None, parse=_day
                ).value
                if last is None or today - last >= refresh:
                    due.append(sid)
            return due

    def run(self, ctx: Context, item: str, *, force: bool) -> Outcome:
        series = ctx.config.series_by_id(item)
        if series is None:
            return Outcome("skipped", "série absente de la config")
        return _SeriesRun(ctx, series, force=force).run()
