import json
import random
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
from rushes.core.phash import is_degenerate_phash, phash_distance
from rushes.db.models import Assignment, Segment
from rushes.db.registry import RegistryRefused, Verdict, assign, can_assign
from rushes.errors import RushesError

TOL = 2000
PHASH_MAX = 10
MIN_SHARE = 0.25
BLACK = "0" * 16
BASE = "5555555555555555"  # 32 bits set: never degenerate, even a few bits away


def bits(n: int) -> str:
    """A 16-hex-char hash at Hamming distance n from BASE."""
    return f"{int(BASE, 16) ^ ((1 << n) - 1):016x}"


def check(session: Session, segment_id: int, account: str) -> Verdict:
    return can_assign(
        session,
        segment_id,
        account,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    )


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
    close = make_segment(session, episode, "part", 0, 10_000, phash=["a" * 16, bits(10)])
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
        phash=[BASE],
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
    held = make_segment(session, episode, "part", 0, 10_000, phash=[BASE])
    assign_raw(session, held, ACCOUNT_SAME_BRAND)
    candidate = make_segment(session, episode, "part", 5_000, 15_000, phash=[BASE])
    assert check(session, candidate.id, ACCOUNT).code == "overlap"  # also pHash-identical


def test_registry_phash_is_checked_before_same_account_part(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    mine = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, mine, ACCOUNT)
    far = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000, phash=[BASE]
    )
    assign_raw(session, far, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(session, episode, "part", 5_000, 15_000, phash=[BASE])
    assert check(session, candidate.id, ACCOUNT).code == "phash"  # also overlaps my own part


def test_r33_simulated_can_assign_writes_nothing(session: Session) -> None:
    episode = make_episode(session, owner=ACCOUNT)
    held_elsewhere = make_segment(session, episode, "part", 0, 10_000)
    assign_raw(session, held_elsewhere, ACCOUNT_SAME_BRAND)
    held_here = make_segment(session, episode, "part", 20_000, 30_000)
    assign_raw(session, held_here, ACCOUNT)
    far = make_segment(
        session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 10_000, phash=[BASE]
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
        wsession,
        ok_segment.id,
        ACCOUNT_OTHER_BRAND,
        at=at,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    )
    assert refused.code == "owner"
    assert count_assignments(wsession) == 0

    granted = assign(
        wsession,
        ok_segment.id,
        ACCOUNT,
        at=at,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    )
    assert granted.ok
    row = wsession.get(Assignment, ok_segment.id)
    assert row is not None
    assert row.account_id == ACCOUNT
    assert row.assigned_at == "2026-11-16T14:30:05+00:00"

    second = assign(
        wsession,
        again.id,
        ACCOUNT,
        at=at,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    )
    assert second.code == "same_account_part"
    assert count_assignments(wsession) == 1
    # a second call on the same segment is refused, not an IntegrityError
    repeat = assign(
        wsession,
        ok_segment.id,
        ACCOUNT,
        at=at,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    )
    assert repeat.code == "same_account_part"


def test_assign_converts_to_utc_and_rejects_naive_datetimes(wsession: Session) -> None:
    episode = make_episode(wsession, owner=ACCOUNT)
    segment = make_segment(wsession, episode, "part", 0, 10_000)
    with pytest.raises(ValueError):
        assign(
            wsession,
            segment.id,
            ACCOUNT,
            at=datetime(2026, 11, 16),
            tolerance_ms=TOL,
            phash_max=1,
            phash_min_share=MIN_SHARE,
        )
    paris = datetime(2026, 11, 16, 15, 0, tzinfo=timezone(timedelta(hours=1)))
    assert assign(
        wsession,
        segment.id,
        ACCOUNT,
        at=paris,
        tolerance_ms=TOL,
        phash_max=1,
        phash_min_share=MIN_SHARE,
    ).ok
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
    assert assign(
        wsession,
        segment.id,
        ACCOUNT,
        at=at,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=MIN_SHARE,
    ).ok
    assert count_assignments(wsession) == 1
    wsession.rollback()  # assign neither committed nor opened a transaction of its own
    assert count_assignments(wsession) == 0


def test_registry_phash_null_means_no_hash(session: Session) -> None:
    other = make_segment(session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 9)
    other.phash_json = "null"
    assign_raw(session, other, ACCOUNT_OTHER_BRAND)
    candidate = make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[BASE]
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
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=[BASE]
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
        assign(
            session,
            segment.id,
            ACCOUNT,
            at=at,
            tolerance_ms=TOL,
            phash_max=PHASH_MAX,
            phash_min_share=MIN_SHARE,
        )
    assert count_assignments(session) == 0
    # the simulation (R33) works on any session
    assert check(session, segment.id, ACCOUNT).ok


# --- R1: comparing the hashes of a whole passage (decision C53) -------------------------------

OTHER_SHOTS = ["3333333333333333"]  # 32 bits away from BASE


def hold(session: Session, hashes: list[str], account: str = ACCOUNT_OTHER_BRAND) -> None:
    """A passage of another account that holds `hashes`."""
    segment = make_segment(session, make_episode(session, owner=account), "part", 0, 10_000)
    segment.phash_json = json.dumps(hashes)
    assign_raw(session, segment, account)


def candidate(session: Session, hashes: list[str]) -> int:
    return make_segment(
        session, make_episode(session, owner=ACCOUNT), "part", 0, 10_000, phash=hashes
    ).id


def fillers(count: int, avoid: list[str], seed: int = 7, gap: int = PHASH_MAX) -> list[str]:
    """Usable hashes at distance > `gap` from every hash in `avoid` and from each other."""
    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < count:
        h = f"{rng.getrandbits(64):016x}"
        if is_degenerate_phash(h):
            continue
        if all(phash_distance(h, other) > gap for other in [*avoid, *out]):
            out.append(h)
    return out


def test_r1_black_frames_of_held_passage_are_ignored(session: Session) -> None:
    hold(session, [BLACK])  # a dim gradient 10 bits from black is a usable hash...
    assert check(session, candidate(session, ["00000000000003ff"]), ACCOUNT).ok  # ...not a match
    hold(session, ["f" * 16], ACCOUNT_SAME_BRAND)
    assert check(session, candidate(session, ["fffffffffffffc00"]), ACCOUNT).ok


def test_r1_black_shots_do_not_dilute_the_share(session: Session) -> None:
    hold(session, [BASE])
    shots = [bits(2), *fillers(3, [BASE]), BLACK, BLACK, BLACK, BLACK]  # 1 of 4 usable, not 1 of 8
    verdict = check(session, candidate(session, shots), ACCOUNT)
    assert verdict.code == "phash"
    assert "1 plan sur 4" in verdict.message


def test_r1_black_frames_never_count_as_similar(session: Session) -> None:
    hold(session, [BLACK, "f" * 16])
    only_black = candidate(session, [BLACK])
    only_white = candidate(session, ["f" * 16])
    both = candidate(session, [BLACK, "f" * 16])
    assert check(session, only_black, ACCOUNT).ok
    assert check(session, only_white, ACCOUNT).ok
    assert check(session, both, ACCOUNT).ok


def test_r1_single_coincidental_shot_among_many_is_accepted(session: Session) -> None:
    # values observed on real episodes: shots at distance 10 of unrelated images
    held_close = ["9964669b9996c966", "9967649c9b896666", "84cd1b996c667965"]
    cand_close = ["9966669999c96966", "99e6669989669966", "c44d1b1b3c6c6d65"]
    for h in cand_close:
        assert min(phash_distance(h, other) for other in held_close) == PHASH_MAX
    avoid = [*held_close, *cand_close]
    held_other = fillers(15, avoid, seed=1)
    hold(session, [*held_close, *held_other])
    cand_others = fillers(16, [*avoid, *held_other], seed=2)
    two_of_18 = candidate(session, [*cand_close[:2], *cand_others])
    one_of_16 = candidate(session, [cand_close[2], *cand_others[:15]])
    assert len(json.loads(session.get_one(Segment, two_of_18).phash_json or "[]")) == 18
    assert check(session, two_of_18, ACCOUNT).ok
    assert check(session, one_of_16, ACCOUNT).ok


def test_r1_passage_sharing_a_quarter_of_its_shots_is_refused(session: Session) -> None:
    hold(session, [BASE])
    others = fillers(3, [BASE])
    verdict = check(session, candidate(session, [bits(2), *others]), ACCOUNT)  # 1 of 4
    assert (verdict.ok, verdict.code) == (False, "phash")


def test_r1_passage_just_under_a_quarter_is_accepted(session: Session) -> None:
    hold(session, [BASE])
    others = fillers(4, [BASE])
    assert check(session, candidate(session, [bits(2), *others]), ACCOUNT).ok  # 1 of 5 = 20 %


def test_r1_passage_with_every_shot_close_is_refused(session: Session) -> None:
    hold(session, [BASE])
    assert check(session, candidate(session, [bits(1)]), ACCOUNT).code == "phash"  # 1 of 1
    assert check(session, candidate(session, [bits(1), bits(3)]), ACCOUNT).code == "phash"


@pytest.mark.parametrize(
    ("share", "close", "total", "refused"),
    [
        (0.1, 1, 10, True),
        (0.1, 1, 11, False),
        (0.3, 3, 10, True),
        (0.3, 3, 11, False),
        (0.07, 7, 100, True),  # 0.07 * 100 is 7.000000000000001 in floating point
        (0.07, 7, 101, False),
    ],
)
def test_r1_share_comparison_is_exact(
    session: Session, share: float, close: int, total: int, refused: bool
) -> None:
    hold(session, [BASE])  # 1/10 and 3/10 sit exactly on the share: no floating-point trap
    shots = [*[bits(i + 1) for i in range(close)], *fillers(total - close, [BASE])]
    verdict = can_assign(
        session,
        candidate(session, shots),
        ACCOUNT,
        tolerance_ms=TOL,
        phash_max=PHASH_MAX,
        phash_min_share=share,
    )
    assert verdict.ok is not refused


def test_r1_close_shots_in_different_passages_do_not_add_up(session: Session) -> None:
    other = "3333333333333333"
    hold(session, [BASE])
    hold(session, [other], ACCOUNT_SAME_BRAND)
    close_to_other = f"{int(other, 16) ^ 0b11111:016x}"
    fill = fillers(3, [BASE, other])
    # 2 close shots out of 5 (40 %) but 1 per passage (20 % each): accepted
    assert check(session, candidate(session, [bits(5), close_to_other, *fill]), ACCOUNT).ok
    # the same two shots in the same passage reach 40 %: refused
    hold(session, [BASE, other], ACCOUNT_OTHER_BRAND)
    verdict = check(session, candidate(session, [bits(5), close_to_other, *fill]), ACCOUNT)
    assert verdict.code == "phash"


def test_r1_passage_with_only_black_shots_is_not_checked(session: Session) -> None:
    hold(session, [BLACK])
    blank = candidate(session, [BLACK, "f" * 16, "0000000000000007"])  # 3 bits set: degenerate
    assert check(session, blank, ACCOUNT).ok
    # a corrupt row elsewhere is not even read, like a passage without hash
    other = make_segment(
        session, make_episode(session, owner=ACCOUNT_SAME_BRAND), "part", 0, 10_000
    )
    other.phash_json = "not json"
    assign_raw(session, other, ACCOUNT_SAME_BRAND)
    assert check(session, blank, ACCOUNT).ok


def test_r1_refusal_message_gives_counts(session: Session) -> None:
    hold(session, [BASE, "3333333333333333"])
    others = fillers(2, [BASE, "3333333333333333"])
    verdict = check(session, candidate(session, [bits(3), bits(4), *others]), ACCOUNT)
    assert verdict.code == "phash"
    assert verdict.message == (
        f"Image proche (distance 3 ; 2 plans sur 4) d'un passage du compte {ACCOUNT_OTHER_BRAND}."
    )


def test_r1_refusal_message_uses_the_singular_for_one_shot(session: Session) -> None:
    hold(session, [BASE])
    verdict = check(session, candidate(session, [bits(3), *fillers(3, [BASE])]), ACCOUNT)
    assert verdict.message == (
        f"Image proche (distance 3 ; 1 plan sur 4) d'un passage du compte {ACCOUNT_OTHER_BRAND}."
    )


def test_r1_phash_distance_boundary_still_inclusive(session: Session) -> None:
    hold(session, [BASE])
    assert check(session, candidate(session, [bits(PHASH_MAX)]), ACCOUNT).code == "phash"
    assert check(session, candidate(session, [bits(PHASH_MAX + 1)]), ACCOUNT).ok


def test_r1_corrupt_hash_of_other_segment_still_raises_with_usable_candidate(
    session: Session,
) -> None:
    other = make_segment(session, make_episode(session, owner=ACCOUNT_OTHER_BRAND), "part", 0, 9)
    other.phash_json = "not json"
    assign_raw(session, other, ACCOUNT_OTHER_BRAND)
    with pytest.raises(RegistryRefused, match=f"Passage {other.id} "):
        check(session, candidate(session, [BASE]), ACCOUNT)


# --- R1 in the other direction: a held passage found again inside the candidate (C53, 2 bis) ---


def pool(count: int, seed: int) -> list[str]:
    """Usable hashes far (> 20 bits) from each other, so a 3-bit variant stays unambiguous."""
    return fillers(count, [], seed=seed, gap=20)


def variant(h: str, bits_changed: int = 3) -> str:
    out = f"{int(h, 16) ^ ((1 << bits_changed) - 1):016x}"
    assert not is_degenerate_phash(out)
    return out


def test_r1_short_gag_copied_inside_a_long_part_is_refused_whatever_the_order(
    session: Session,
) -> None:
    shots = pool(33, seed=11)
    gag, rest = shots[:3], shots[3:]
    long_part = [*[variant(h) for h in gag], *rest]  # 3 of 30 shots, 10 %
    # the gag is held first: the long part is refused from the held side
    hold(session, gag)
    verdict = check(session, candidate(session, long_part), ACCOUNT)
    assert (verdict.ok, verdict.code) == (False, "phash")
    assert verdict.message == (
        "Image proche (distance 3) : 3 plans sur 3 d'un passage "
        f"du compte {ACCOUNT_OTHER_BRAND} se retrouvent dans ce passage."
    )


def test_r1_same_verdict_when_the_long_part_is_held_first(session: Session) -> None:
    shots = pool(33, seed=11)
    gag, rest = shots[:3], shots[3:]
    hold(session, [*[variant(h) for h in gag], *rest])
    verdict = check(session, candidate(session, gag), ACCOUNT)  # 3 of 3 from the candidate side
    assert (verdict.ok, verdict.code) == (False, "phash")


def test_r1_held_passage_of_two_shots_is_only_compared_from_the_candidate_side(
    session: Session,
) -> None:
    shots = pool(32, seed=12)
    hold(session, shots[:2])
    long_part = [variant(shots[0]), *shots[2:]]  # 1 of 2 held shots (50 %) but 1 of 31 of mine
    assert check(session, candidate(session, long_part), ACCOUNT).ok


@pytest.mark.parametrize(("held_size", "refused"), [(3, True), (4, True), (5, False)])
def test_r1_held_passage_share_is_compared_from_its_side(
    session: Session, held_size: int, refused: bool
) -> None:
    shots = pool(20 + held_size, seed=13)
    held, rest = shots[:held_size], shots[held_size:]
    hold(session, held)  # 1 of 3 (33 %), 1 of 4 (25 %) refuse; 1 of 5 (20 %) accepts
    verdict = check(session, candidate(session, [variant(held[0]), *rest]), ACCOUNT)
    assert verdict.ok is not refused
    if refused:
        assert verdict.message == (
            f"Image proche (distance 3) : 1 plan sur {held_size} d'un passage "
            f"du compte {ACCOUNT_OTHER_BRAND} se retrouve dans ce passage."
        )


def test_r1_one_candidate_shot_close_to_two_held_shots_counts_for_both(session: Session) -> None:
    shots = pool(40, seed=15)
    twin = variant(shots[0], 2)  # a second held shot of the same decor, 2 bits from the first
    hold(session, [shots[0], twin, *shots[1:7]])  # 8 held shots
    one = variant(shots[0], 1)  # within 10 bits of both twins
    verdict = check(session, candidate(session, [one, *shots[10:]]), ACCOUNT)
    assert verdict.code == "phash"  # 2 of 8 held shots (25 %), though 1 of 31 of mine
    assert "2 plans sur 8" in verdict.message


def test_r1_close_shots_in_different_held_passages_do_not_add_up_either_way(
    session: Session,
) -> None:
    shots = pool(40, seed=14)
    first, second, rest = shots[:5], shots[5:10], shots[10:]
    hold(session, first)
    hold(session, second, ACCOUNT_SAME_BRAND)
    # one shot of each held passage (20 % of each) found in the candidate: accepted
    spread = [variant(first[0]), variant(second[0]), *rest]
    assert check(session, candidate(session, spread), ACCOUNT).ok
    # two shots of the same held passage (40 %): refused
    twice = [variant(first[0]), variant(first[1]), *rest]
    assert check(session, candidate(session, twice), ACCOUNT).code == "phash"
