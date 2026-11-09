"""`assign` step (task 8a): approved parts go to the owner account of their episode (R1, R3).

Only `part` segments are taken here: best-of and compilation items are assigned with their
compilation (task 13, R2). The registry decides, inside a `BEGIN IMMEDIATE` transaction that also
inserts the post, so the check, the assignment and the post are atomic across processes.
"""

from typing import ClassVar

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from rushes.config import Config, Layout
from rushes.context import Context, Filters
from rushes.core.timecode import s_to_ms
from rushes.db.engine import write_session
from rushes.db.models import Assignment, Episode, Post, Segment, Series
from rushes.db.registry import assign
from rushes.ids import segment_key
from rushes.runner import BaseStep, Outcome

DEFAULT_LAYOUT: Layout = "square"  # R8, when the config lists no preference


def default_layout(config: Config) -> Layout:
    """First layout of the preference list (accounts.yaml), else `square` (C45)."""
    preference = config.accounts.layouts.preference
    return preference[0] if preference else DEFAULT_LAYOUT


def is_test_post(config: Config, session: Session, episode_id: int) -> bool:
    """True when the post must stay in `outbox_test/` (R14, R22, R32).

    A series is a test unless the config says `licensed: true` and the prospection did not find
    it; a creator is a test unless the config says `authorized: true`. What the config does not
    know (a candidate found by `discover`) is a test."""
    episode = session.get_one(Episode, episode_id)
    if episode.series_id is not None:
        series = config.series_by_id(episode.series_id)
        row = session.get(Series, episode.series_id)
        return (
            series is None or not series.licensed or (row is not None and row.origin == "prospect")
        )
    creator = config.creator_by_id(episode.creator_id or "")
    return creator is None or not creator.authorized


class AssignStep(BaseStep):
    name: ClassVar[str] = "assign"

    def key(self, item: int) -> str:
        return segment_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Approved parts nobody holds yet (R3); --force changes nothing here (R1)."""
        query = (
            select(Segment.id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(
                Segment.kind == "part",
                Segment.status == "approved",
                ~exists().where(Assignment.segment_id == Segment.id),
            )
            .order_by(Segment.id)
        )
        if filters.episode is not None:
            query = query.where(Episode.id == filters.episode)
        if filters.episodes:
            query = query.where(Episode.id.in_(filters.episodes))
        if filters.series is not None:
            query = query.where(Episode.series_id == filters.series)
        if filters.creator is not None:
            query = query.where(Episode.creator_id == filters.creator)
        if filters.account is not None:
            query = query.where(Episode.owner_account_id == filters.account)
        with Session(ctx.engine) as session:
            return list(session.scalars(query))

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = segment_key(item)
        registry = ctx.config.settings.registry
        layout = default_layout(ctx.config)
        with write_session(
            ctx.engine
        ) as session:  # takes the write lock: check + insert are atomic
            segment = session.get(Segment, item)
            if segment is None:
                return Outcome("skipped", "passage introuvable")
            if segment.kind != "part" or segment.status != "approved":  # R2, R3
                return Outcome(
                    "skipped", f"{key} est {segment.kind} {segment.status} : non attribué"
                )
            if session.get(Assignment, item) is not None:
                return Outcome("skipped", f"{key} est déjà attribué")
            episode = session.get_one(Episode, segment.episode_id)
            owner = episode.owner_account_id
            verdict = assign(
                session,
                item,
                owner,
                at=ctx.clock.now(),
                tolerance_ms=s_to_ms(registry.overlap_tolerance_s),
                phash_max=registry.phash_max_distance,
            )
            if not verdict.ok:
                session.rollback()
                return Outcome("warned", f"refus du registre : {verdict.message}")
            session.add(
                Post(
                    account_id=owner,
                    segment_id=item,
                    family=segment.family,
                    layout=layout,
                    originality=ctx.config.effective_originality(owner),
                    status="planned",
                    is_test=is_test_post(ctx.config, session, episode.id),
                )
            )
            session.commit()
        return Outcome("done", f"attribué à {owner}")
