"""Uniqueness registry (R1, R2, R27, R44): the only place that decides who may hold a segment."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.core.intervals import overlap_ms
from rushes.core.phash import is_phash, min_phash_distance
from rushes.db.engine import WRITE_LOCK
from rushes.db.models import Account, Assignment, Episode, Segment
from rushes.errors import RushesError

VerdictCode = Literal["ok", "owner", "overlap", "phash", "same_account_part"]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: VerdictCode
    message: str


class RegistryRefused(RushesError):
    """Programming error (unknown segment or account); a refusal is a Verdict, not this."""


_OK = Verdict(True, "ok", "attribution possible")


def _refuse(code: VerdictCode, message: str) -> Verdict:
    return Verdict(False, code, message)


def _hashes(segment_id: int, raw: str | None) -> list[str]:
    """Key-frame hashes from `phash_json`; NULL or JSON null means no hash, a bad shape raises."""
    message = f"Passage {segment_id} : phash_json invalide (liste de pHash attendue)."
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RegistryRefused(message) from None
    if data is None:
        return []
    if not isinstance(data, list) or not all(is_phash(h) for h in data):
        raise RegistryRefused(message)
    return [str(h) for h in data]


def can_assign(
    session: Session, segment_id: int, account_id: str, *, tolerance_ms: int, phash_max: int
) -> Verdict:
    """Check R44, R1 then R2 for this segment and account. Never writes."""
    segment = session.get(Segment, segment_id)
    if segment is None:
        raise RegistryRefused(f"Passage {segment_id} introuvable.")
    if session.get(Account, account_id) is None:
        raise RegistryRefused(f"Compte {account_id} introuvable.")
    mine = _hashes(segment.id, segment.phash_json)  # a corrupt row is reported before any verdict
    episode = session.get(Episode, segment.episode_id)
    if episode is None:  # the foreign key forbids it
        raise RegistryRefused(f"Épisode {segment.episode_id} introuvable.")

    if episode.owner_account_id != account_id:
        return _refuse(
            "owner",
            f"L'épisode appartient au compte {episode.owner_account_id}, pas à {account_id}.",
        )

    span = (segment.start_ms, segment.end_ms)
    held_in_episode = session.execute(
        select(Segment.id, Segment.start_ms, Segment.end_ms, Assignment.account_id)
        .join(Assignment, Assignment.segment_id == Segment.id)
        .where(Assignment.account_id != account_id, Segment.episode_id == segment.episode_id)
        .order_by(Segment.id)
    ).all()
    for other_id, other_start, other_end, other_account in held_in_episode:
        if other_id == segment.id:  # already held elsewhere, whatever its length
            return _refuse("overlap", f"Le passage est déjà attribué au compte {other_account}.")
        common = overlap_ms(span, (other_start, other_end))
        if common > tolerance_ms:
            return _refuse(
                "overlap",
                f"Chevauche de {common} ms un passage attribué au compte {other_account}.",
            )

    # Compared in Python: fine for a few thousand assigned segments; rows without a hash are
    # skipped in SQL, and nothing is read when the candidate has none.
    if mine:
        held_with_hash = session.execute(
            select(Segment.id, Segment.phash_json, Assignment.account_id)
            .join(Assignment, Assignment.segment_id == Segment.id)
            .where(Assignment.account_id != account_id, Segment.phash_json.is_not(None))
            .order_by(Segment.id)
        ).all()
        for other_id, other_json, other_account in held_with_hash:
            distance = min_phash_distance(mine, _hashes(other_id, other_json))
            if distance is not None and distance <= phash_max:
                return _refuse(
                    "phash",
                    f"Image proche (distance {distance}) d'un passage du compte {other_account}.",
                )

    held_here = session.execute(
        select(Segment.id, Segment.kind, Segment.start_ms, Segment.end_ms)
        .join(Assignment, Assignment.segment_id == Segment.id)
        .where(Assignment.account_id == account_id, Segment.episode_id == segment.episode_id)
        .order_by(Segment.id)
    ).all()
    for other_id, other_kind, other_start, other_end in held_here:
        if other_id == segment.id:
            return _refuse("same_account_part", f"Le passage est déjà attribué à {account_id}.")
        if (
            segment.kind == "part"
            and other_kind == "part"
            and overlap_ms(span, (other_start, other_end)) > tolerance_ms
        ):
            return _refuse(
                "same_account_part",
                f"Chevauche une autre partie déjà attribuée à {account_id}.",
            )
    return _OK


def assign(
    session: Session,
    segment_id: int,
    account_id: str,
    *,
    at: datetime,
    tolerance_ms: int,
    phash_max: int,
) -> Verdict:
    """can_assign, then the insert in the caller's transaction; nothing is written on refusal.

    The session must come from `db.engine.write_session`: its transaction holds the write lock
    from the first statement, so the check and the insert are atomic across processes (R1).
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être une date avec fuseau horaire")
    if session.connection().info.get(WRITE_LOCK) is not True:
        raise RegistryRefused(
            "L'attribution exige une session ouverte par write_session (BEGIN IMMEDIATE) : "
            "sinon le contrôle et l'insertion ne sont pas atomiques."
        )
    verdict = can_assign(
        session, segment_id, account_id, tolerance_ms=tolerance_ms, phash_max=phash_max
    )
    if verdict.ok:
        session.add(
            Assignment(
                segment_id=segment_id,
                account_id=account_id,
                assigned_at=at.astimezone(UTC).isoformat(timespec="seconds"),
            )
        )
        session.flush()
    return verdict
