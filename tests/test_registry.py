from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT,
    ACCOUNT_OTHER_BRAND,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    CREATOR_ACCOUNT,
    CREATOR_ACCOUNT_OTHER_BRAND,
    CREATOR_ACCOUNT_SAME_BRAND,
    CREATOR_OTHER,
    assign_raw,
    make_episode,
    make_segment,
)
from rushes.db.models import Assignment
from rushes.db.registry import RegistryRefused, Verdict, assign, can_assign
from rushes.errors import RushesError

TOL = 2000
PHASH_MAX = 10
ZERO = "0" * 16


def bits(n: int) -> str:
    """A 16-hex-char hash at Hamming distance n from ZERO."""
    return f"{(1 << n) - 1:016x}"


def check(session: Session, segment_id: int, account: str) -> Verdict:
    return can_assign(session, segment_id, account, tolerance_ms=TOL, phash_max=PHASH_MAX)


def count_assignments(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Assignment)) or 0


def test_r1_overlap_over_2s_other_account_refused(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, episode, "part", 7_999, 20_000)  # overlap 2001 ms
    verdict = check(session, candidate.id, ACCOUNT)
    assert not verdict.ok
    assert verdict.code == "overlap"
    assert verdict.message


def test_r1_overlap_within_tolerance_allowed(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, episode, "part", 8_000, 20_000)  # overlap 2000 ms
    verdict = check(session, candidate.id, ACCOUNT)
    assert verdict.ok
    assert verdict.code == "ok"


def test_r1_candidate_already_held_by_other_account_is_an_overlap(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held = make_segment(session, episode, "part", 0, 1_000)  # shorter than the tolerance
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    assert check(session, held.id, ACCOUNT).code == "overlap"


def test_r1_overlap_ignores_other_episodes(session: Session) -> None:
    held = make_segment(session, make_episode(session, owner=ACCOUNT_SAME_BRAND), "part", 0, 10_000)
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000)
    assert check(session, candidate.id, ACCOUNT).ok


def test_r1_phash_close_other_account_refused(session: Session) -> None:
    held = make_segment(
        session,
        make_episode(session, owner=ACCOUNT_OTHER_BRAND),
        "part",
        0,
        10_000,
        phash=[bits(0)],
    )
    assign_raw(session, held, ACCOUNT_OTHER_BRAND)
    episode = make_episode(session, owner=ACCOUNT)
    close = make_segment(session, episode, "part", 0, 10_000, phash=["f" * 16, bits(10)])
    far = make_segment(session, episode, "part", 20_000, 30_000, phash=[bits(11)])
    no_hash = make_segment(session, episode, "part", 40_000, 50_000)
    assert check(session, close.id, ACCOUNT).code == "phash"  # distance 10: refused
    assert check(session, far.id, ACCOUNT).ok  # distance 11: accepted
    assert check(session, no_hash.id, ACCOUNT).ok  # no hash on one side: no refusal


def test_r1_phash_of_same_account_is_not_compared(session: Session) -> None:
    held = make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[bits(0)]
    )
    assign_raw(session, held, ACCOUNT)
    candidate = make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[bits(0)]
    )
    assert check(session, candidate.id, ACCOUNT).ok


def test_r2_part_and_bestof_same_account_allowed(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    part = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, part, ACCOUNT)
    bestof = make_segment(session, episode, "bestof_item", 1_000, 9_000)
    compilation = make_segment(session, episode, "compilation_item", 2_000, 8_000)
    assert check(session, bestof.id, ACCOUNT).ok
    assert check(session, compilation.id, ACCOUNT).ok


def test_r2_two_parts_same_passage_refused(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    first = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, first, ACCOUNT)
    second = make_segment(session, episode, "part", 5_000, 15_000)
    adjacent = make_segment(session, episode, "part", 10_000, 20_000)
    within_tolerance = make_segment(session, episode, "part", 8_000, 20_000)
    verdict = check(session, second.id, ACCOUNT)
    assert not verdict.ok
    assert verdict.code == "same_account_part"
    assert check(session, adjacent.id, ACCOUNT).ok
    assert check(session, within_tolerance.id, ACCOUNT).ok  # overlap 2000 ms: tolerated


def test_r2_segment_already_assigned_to_this_account_refused(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    part = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, part, ACCOUNT)
    verdict = check(session, part.id, ACCOUNT)
    assert verdict.code == "same_account_part"
    assert "déjà" in verdict.message


def test_r27_registry_applies_to_creators(session: Session) -> None:
    episode = make_episode(session, owner=CREATOR_ACCOUNT, creator_id=CREATOR)
    assert episode.series_id is None
    held = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held, CREATOR_ACCOUNT_SAME_BRAND)
    over = make_segment(session, episode, "part", 7_999, 20_000)
    edge = make_segment(session, episode, "part", 8_000, 20_000)
    assert check(session, over.id, CREATOR_ACCOUNT).code == "overlap"
    assert check(session, edge.id, CREATOR_ACCOUNT).ok
    assert (
        check(session, over.id, CREATOR_ACCOUNT_OTHER_BRAND).code == "owner"
    )  # another brand's account (R44)
    # pHash across creator episodes
    other = make_segment(
        session,
        make_episode(session, owner=CREATOR_ACCOUNT_OTHER_BRAND, creator_id=CREATOR_OTHER),
        "part",
        0,
        10_000,
        phash=[ZERO],
    )
    assign_raw(session, other, CREATOR_ACCOUNT_OTHER_BRAND)
    twin = make_segment(session, episode, "part", 30_000, 40_000, phash=[bits(10)])
    assert check(session, twin.id, CREATOR_ACCOUNT).code == "phash"
    # R2 on a creator account
    mine = make_segment(session, episode, "part", 50_000, 60_000)
    assign_raw(session, mine, CREATOR_ACCOUNT)
    again = make_segment(session, episode, "part", 55_000, 65_000)
    assert check(session, again.id, CREATOR_ACCOUNT).code == "same_account_part"


def test_r44_owner_mismatch_refused(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    segment = make_segment(session, episode, "part", 0, 10_000)
    verdict = check(session, segment.id, ACCOUNT_OTHER_BRAND)
    assert not verdict.ok
    assert verdict.code == "owner"
    assert (
        check(session, segment.id, ACCOUNT_SAME_BRAND).code == "owner"
    )  # same brand, other account
    assert check(session, segment.id, ACCOUNT).ok


def test_registry_checks_run_in_order_owner_first(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, episode, "part", 0, 10_000)
    assert check(session, candidate.id, ACCOUNT_OTHER_BRAND).code == "owner"  # also overlapping
    assert check(session, candidate.id, ACCOUNT).code == "overlap"


def test_registry_overlap_is_checked_before_phash(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held = make_segment(session, episode, "part", 0, 10_000, phash=[ZERO])
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, episode, "part", 5_000, 15_000, phash=[ZERO])
    assert check(session, candidate.id, ACCOUNT).code == "overlap"  # also pHash-identical


def test_registry_phash_is_checked_before_same_account_part(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    mine = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, mine, ACCOUNT)
    far = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000, phash=[ZERO]
    )
    assign_raw(session, far, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(session, episode, "part", 5_000, 15_000, phash=[ZERO])
    assert check(session, candidate.id, ACCOUNT).code == "phash"  # also overlaps my own part


def test_r33_simulated_can_assign_writes_nothing(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held_elsewhere = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held_elsewhere, ACCOUNT_SAME_BRAND)
    held_here = make_segment(session, episode, "part", 20_000, 30_000)
    assign_raw(session, held_here, ACCOUNT)
    far = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000, phash=[ZERO]
    )
    assign_raw(session, far, ACCOUNT_OTHER_BRAND)
    ok = make_segment(session, episode, "part", 40_000, 50_000)
    overlap = make_segment(session, episode, "part", 5_000, 15_000)
    phash = make_segment(session, episode, "part", 60_000, 70_000, phash=[bits(3)])
    same_part = make_segment(session, episode, "part", 25_000, 35_000)
    session.commit()
    before = count_assignments(session)
    codes = {
        "ok": check(session, ok.id, ACCOUNT).code,
        "owner": check(session, ok.id, ACCOUNT_OTHER_BRAND).code,
        "overlap": check(session, overlap.id, ACCOUNT).code,
        "phash": check(session, phash.id, ACCOUNT).code,
        "same_account_part": check(session, same_part.id, ACCOUNT).code,
    }
    assert codes == {name: name for name in codes}
    assert count_assignments(session) == before
    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_assign_inserts_only_when_ok(wsession: Session) -> None:
    episode = make_episode(wsession, owner=ACCOUNT)
    ok_segment = make_segment(wsession, episode, "part", 0, 10_000)
    again = make_segment(wsession, episode, "part", 5_000, 15_000)
    at = datetime(2026, 11, 16, 14, 30, 5, tzinfo=UTC)

    refused = assign(
        wsession, ok_segment.id, ACCOUNT_OTHER_BRAND, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX
    )
    assert refused.code == "owner"
    assert count_assignments(wsession) == 0

    granted = assign(wsession, ok_segment.id, ACCOUNT, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX)
    assert granted.ok
    row = wsession.get(Assignment, ok_segment.id)
    assert row is not None
    assert row.account_id == ACCOUNT
    assert row.assigned_at == "2026-11-16T14:30:05+00:00"

    second = assign(wsession, again.id, ACCOUNT, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX)
    assert second.code == "same_account_part"
    assert count_assignments(wsession) == 1
    # a second call on the same segment is refused, not an IntegrityError
    repeat = assign(wsession, ok_segment.id, ACCOUNT, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX)
    assert repeat.code == "same_account_part"


def test_assign_converts_to_utc_and_rejects_naive_datetimes(wsession: Session) -> None:
    episode = make_episode(wsession, owner=ACCOUNT)
    segment = make_segment(wsession, episode, "part", 0, 10_000)
    with pytest.raises(ValueError):
        assign(
            wsession, segment.id, ACCOUNT, at=datetime(2026, 11, 16), tolerance_ms=TOL, phash_max=1
        )
    paris = datetime(2026, 11, 16, 15, 0, tzinfo=timezone(timedelta(hours=1)))
    assert assign(wsession, segment.id, ACCOUNT, at=paris, tolerance_ms=TOL, phash_max=1).ok
    row = wsession.get(Assignment, segment.id)
    assert row is not None
    assert row.assigned_at == "2026-11-16T14:00:00+00:00"


def test_unknown_segment_or_account_is_a_programming_error(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    segment = make_segment(session, episode, "part", 0, 10_000)
    with pytest.raises(RegistryRefused, match="introuvable"):
        check(session, 999_999, ACCOUNT)
    with pytest.raises(RegistryRefused, match="introuvable"):
        check(session, segment.id, "nope")
    assert issubclass(RegistryRefused, RushesError)


def test_assign_stays_in_the_callers_transaction(wsession: Session) -> None:
    segment = make_segment(wsession, make_episode(wsession, owner=ACCOUNT), "part", 0, 10_000)
    wsession.commit()
    at = datetime(2026, 11, 16, 14, 0, tzinfo=UTC)
    assert assign(wsession, segment.id, ACCOUNT, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX).ok
    assert count_assignments(wsession) == 1
    wsession.rollback()  # assign neither committed nor opened a transaction of its own
    assert count_assignments(wsession) == 0


def test_registry_phash_null_means_no_hash(session: Session) -> None:
    other = make_segment(session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 9)
    other.phash_json = "null"
    assign_raw(session, other, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[ZERO]
    )
    assert check(session, candidate.id, ACCOUNT).ok
    candidate.phash_json = "null"
    session.flush()
    assert check(session, candidate.id, ACCOUNT).ok


CORRUPT = ["not json", "[1]", '"0000000000000000"', '["xyz"]', '{"a": 1}', '["' + "0" * 17 + '"]']


@pytest.mark.parametrize("raw", CORRUPT)
def test_registry_corrupt_phash_of_other_segment_names_it(session: Session, raw: str) -> None:
    other = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000
    )
    other.phash_json = raw
    assign_raw(session, other, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[ZERO]
    )
    with pytest.raises(RegistryRefused, match=f"Passage {other.id} "):
        check(session, candidate.id, ACCOUNT)


@pytest.mark.parametrize("raw", CORRUPT)
def test_registry_corrupt_phash_of_candidate_names_it(session: Session, raw: str) -> None:
    candidate = make_segment(session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000)
    candidate.phash_json = raw
    session.flush()
    with pytest.raises(RegistryRefused, match=f"Passage {candidate.id} "):
        check(session, candidate.id, ACCOUNT)


def test_registry_candidate_without_hash_ignores_corrupt_rows_elsewhere(session: Session) -> None:
    other = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000
    )
    other.phash_json = "not json"
    assign_raw(session, other, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000)
    assert check(session, candidate.id, ACCOUNT).ok


def test_registry_corrupt_phash_of_candidate_is_detected_before_any_verdict(
    session: Session,
) -> None:
    candidate = make_segment(session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000)
    candidate.phash_json = "not json"
    session.flush()
    with pytest.raises(RegistryRefused, match=f"Passage {candidate.id} "):
        check(session, candidate.id, ACCOUNT_OTHER_BRAND)  # would be an "owner" refusal


def test_r1_assign_refuses_a_session_without_the_write_lock(session: Session) -> None:
    segment = make_segment(session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000)
    at = datetime(2026, 11, 16, 14, 0, tzinfo=UTC)
    with pytest.raises(RegistryRefused, match="write_session"):
        assign(session, segment.id, ACCOUNT, at=at, tolerance_ms=TOL, phash_max=PHASH_MAX)
    assert count_assignments(session) == 0
    # the simulation (R33) works on any session
    assert check(session, segment.id, ACCOUNT).ok
