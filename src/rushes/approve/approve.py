"""`approve` step, version 0 (task 8a; task 15 replaces it with the scored approval).

A proposed part is approved when R6 (duration), R5 (no cut in speech) and R1 (simulated with
`can_assign`, nothing written) hold; otherwise it goes to `needs_review` and the event says why.
The score is not compared with the threshold yet, and R24 and R34 have no data yet.
"""

from typing import ClassVar

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.context import Context, Filters
from rushes.core.timecode import s_to_ms
from rushes.db.models import Episode, Segment
from rushes.db.registry import can_assign
from rushes.ids import episode_key, segment_key
from rushes.runner import BaseStep, Outcome
from rushes.schemas import AudioFile, read_json


def _speech_violations(ctx: Context, segment: Segment, episode: Episode) -> list[str]:
    """R5: a cut (segment start and end, except the episode's own edges) is not in speech."""
    folder = ctx.paths.episode_dir(episode_key(episode.id))
    try:
        audio = read_json(AudioFile, folder / "audio.json")
    except OSError, ValueError:
        return [f"R5 : audio.json illisible pour {episode_key(episode.id)}, parole non vérifiable"]
    margin = ctx.config.settings.cut.speech_margin_ms
    cuts = [
        (label, at)
        for label, at in (("début", segment.start_ms), ("fin", segment.end_ms))
        if 0 < at < episode.duration_ms
    ]
    return [
        f"R5 : coupe de {label} à {at / 1000:g} s pendant la parole"
        for label, at in cuts
        if any(a - margin <= at <= b + margin for a, b in audio.speech)
    ]


def rule_violations(session: Session, segment_id: int, ctx: Context) -> list[str]:
    """What keeps a segment from approval (French messages); empty when it can be approved."""
    segment = session.get_one(Segment, segment_id)
    episode = session.get_one(Episode, segment.episode_id)
    found: list[str] = []
    family = ctx.config.accounts.families.get(segment.family)
    duration_ms = segment.end_ms - segment.start_ms
    if family is None:
        found.append(f"R6 : famille inconnue ({segment.family})")
    elif not s_to_ms(family.min_s) <= duration_ms <= s_to_ms(family.max_s):
        found.append(
            f"R6 : durée de {duration_ms / 1000:g} s hors des bornes "
            f"{family.min_s:g}-{family.max_s:g} s"
        )
    found += _speech_violations(ctx, segment, episode)
    registry = ctx.config.settings.registry
    verdict = can_assign(
        session,
        segment_id,
        episode.owner_account_id,
        tolerance_ms=s_to_ms(registry.overlap_tolerance_s),
        phash_max=registry.phash_max_distance,
    )
    if not verdict.ok:
        found.append(f"R1 : {verdict.message}")
    return found


class ApproveStep(BaseStep):
    name: ClassVar[str] = "approve"

    def key(self, item: int) -> str:
        return segment_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Proposed parts; with --force the ones sent to review too (rules may have changed)."""
        states = ["proposed", "needs_review"] if filters.force else ["proposed"]
        query = (
            select(Segment.id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(Segment.kind == "part", Segment.status.in_(states))
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
        with Session(ctx.engine) as session:
            segment = session.get(Segment, item)
            if segment is None:
                return Outcome("skipped", "passage introuvable")
            seen = segment.status
            if segment.kind != "part" or seen not in ("proposed", "needs_review"):
                return Outcome("skipped", f"{key} est {seen} : rien à approuver")
            violations = rule_violations(session, item, ctx)
        new = "needs_review" if violations else "approved"
        with ctx.engine.begin() as conn:  # only the row as it was read
            done = conn.execute(
                text("UPDATE segments SET status=:new WHERE id=:id AND status=:seen"),
                {"new": new, "id": item, "seen": seen},
            )
        if done.rowcount != 1:
            return Outcome("skipped", f"{key} a changé pendant le contrôle")
        if violations:
            return Outcome("warned", "à revoir : " + " ; ".join(violations))
        return Outcome("done", "approuvé")
