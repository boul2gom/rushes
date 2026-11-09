"""Approval v0 (replaced by task 15): approved when R1, R5 and R6 hold, else needs_review."""

from typing import Any

from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    analyzed_episode,
    assign_raw,
    ctx_events,
    make_segment,
    snapshot,
)
from rushes.approve.approve import ApproveStep, rule_violations
from rushes.context import Context, Filters
from rushes.db.models import Episode, Segment
from rushes.runner import StepReport, run_step

EPISODE_MS = 600_000


def _episode(ctx: Context, **kwargs: Any) -> int:
    return analyzed_episode(ctx, duration_ms=EPISODE_MS, **kwargs)


def _segment(ctx: Context, episode: int, start: int, end: int, kind: str = "part") -> int:
    with Session(ctx.engine) as session:
        ep = session.get_one(Episode, episode)
        segment = make_segment(session, ep, kind, start, end)
        segment.status = "proposed"
        session.commit()
        return segment.id


def _status(ctx: Context, segment_id: int) -> str:
    with Session(ctx.engine) as session:
        return session.get_one(Segment, segment_id).status


def _approve(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, ApproveStep(), Filters(**filters))


def _messages(ctx: Context) -> list[str]:
    return [str(e.get("message", "")) for e in ctx_events(ctx) if e.get("step") == "approve"]


def test_approve_v0_approves_a_clean_segment(ctx: Context) -> None:
    segment = _segment(ctx, _episode(ctx), 10_000, 80_000)  # 70 s, no speech
    report = _approve(ctx)
    assert (report.done, report.warned, report.failed) == (1, 0, 0)
    assert _status(ctx, segment) == "approved"


def test_approve_v0_flags_rule_violation(ctx: Context) -> None:
    segment = _segment(ctx, _episode(ctx), 10_000, 40_000)  # 30 s: under the 65 s of R6
    report = _approve(ctx)
    assert (report.done, report.warned) == (0, 1)
    assert _status(ctx, segment) == "needs_review"
    assert any("R6" in m and "30" in m for m in _messages(ctx))  # the violation is in the event


def test_r6_bounds_are_inclusive(ctx: Context) -> None:
    episode = _episode(ctx)
    low = _segment(ctx, episode, 0, 65_000)
    high = _segment(ctx, episode, 100_000, 200_000)
    too_low = _segment(ctx, episode, 300_000, 364_999)
    too_high = _segment(ctx, episode, 400_000, 500_001)
    _approve(ctx)
    assert [_status(ctx, s) for s in (low, high, too_low, too_high)] == [
        "approved", "approved", "needs_review", "needs_review",
    ]  # fmt: skip


def test_r5_cut_inside_speech_is_flagged(ctx: Context) -> None:
    episode = _episode(ctx, speech=[(70_000, 75_000), (270_000, 271_000)])
    inside = _segment(ctx, episode, 10_000, 72_000)  # ends in the middle of a sentence
    margin = _segment(ctx, episode, 200_000, 269_900)  # speech starts 100 ms later, margin 150
    clear = _segment(ctx, episode, 300_000, 369_000)
    _approve(ctx)
    assert _status(ctx, inside) == "needs_review"
    assert any("R5" in m for m in _messages(ctx))
    assert _status(ctx, clear) == "approved"
    assert _status(ctx, margin) == "needs_review"  # ends 100 ms before speech: inside the margin


def test_r5_start_inside_speech_and_margin(ctx: Context) -> None:
    episode = _episode(ctx, speech=[(5_000, 6_000)])
    start_in = _segment(ctx, episode, 5_500, 80_000)
    start_margin = _segment(ctx, episode, 6_100, 80_000)  # within 150 ms after the speech
    start_clear = _segment(ctx, episode, 6_200, 80_000)
    _approve(ctx)
    assert _status(ctx, start_in) == "needs_review"
    assert _status(ctx, start_margin) == "needs_review"
    assert _status(ctx, start_clear) == "approved"


def test_r5_episode_edges_are_not_cuts(ctx: Context) -> None:
    episode = _episode(ctx, speech=[(0, 500), (EPISODE_MS - 500, EPISODE_MS)])
    first = _segment(ctx, episode, 0, 70_000)
    last = _segment(ctx, episode, EPISODE_MS - 70_000, EPISODE_MS)
    _approve(ctx)
    assert _status(ctx, first) == "approved" and _status(ctx, last) == "approved"


def test_r5_missing_audio_file_cannot_be_checked(ctx: Context) -> None:
    episode = _episode(ctx)
    segment = _segment(ctx, episode, 10_000, 80_000)
    (ctx.paths.episode_dir(f"e_{episode:06d}") / "audio.json").unlink()
    _approve(ctx)
    assert _status(ctx, segment) == "needs_review"
    assert any("audio.json" in m for m in _messages(ctx))


def test_r1_simulated_overlap_with_another_account_is_flagged(ctx: Context) -> None:
    episode = _episode(ctx)
    held = _segment(ctx, episode, 0, 70_000)
    candidate = _segment(ctx, episode, 60_000, 130_000)  # overlaps by 10 s > tolerance
    with Session(ctx.engine) as session:
        assign_raw(session, session.get_one(Segment, held), ACCOUNT_SAME_BRAND)
        session.commit()
        violations = rule_violations(session, candidate, ctx)
    assert any(v.startswith("R1") for v in violations)
    _approve(ctx)
    assert _status(ctx, candidate) == "needs_review"


def test_r1_simulation_writes_nothing(ctx: Context) -> None:
    episode = _episode(ctx)
    segment = _segment(ctx, episode, 10_000, 80_000)
    before = snapshot(ctx)["table:assignments"]
    with Session(ctx.engine) as session:
        assert rule_violations(session, segment, ctx) == []
    assert snapshot(ctx)["table:assignments"] == before == []


def test_approve_v0_ignores_the_score(ctx: Context) -> None:
    segment = _segment(ctx, _episode(ctx), 10_000, 80_000)
    with Session(ctx.engine) as session:
        session.get_one(Segment, segment).score = 0.0
        session.commit()
    _approve(ctx)
    assert _status(ctx, segment) == "approved"  # the threshold arrives with task 15


def test_approve_v0_only_looks_at_proposed_parts(ctx: Context) -> None:
    episode = _episode(ctx)
    ids = {
        status: _segment(ctx, episode, 10_000 + i * 100_000, 80_000 + i * 100_000)
        for i, status in enumerate(("approved", "rejected", "needs_review"))
    }
    bestof = _segment(ctx, episode, 10_000, 80_000, kind="bestof_item")
    with Session(ctx.engine) as session:
        for status, segment_id in ids.items():
            session.get_one(Segment, segment_id).status = status
        session.commit()
    assert _approve(ctx).done == 0
    assert [_status(ctx, i) for i in ids.values()] == ["approved", "rejected", "needs_review"]
    assert _status(ctx, bestof) == "proposed"


def test_approve_v0_rerun_changes_nothing(ctx: Context) -> None:
    episode = _episode(ctx)
    _segment(ctx, episode, 10_000, 80_000)
    _segment(ctx, episode, 100_000, 130_000)
    _approve(ctx)
    before = snapshot(ctx)
    second = _approve(ctx)
    assert (second.done, second.warned, second.skipped, second.failed) == (0, 0, 0, 0)
    assert snapshot(ctx) == before


def test_approve_v0_force_reevaluates_needs_review(ctx: Context) -> None:
    episode = _episode(ctx)
    segment = _segment(ctx, episode, 10_000, 80_000)
    (ctx.paths.episode_dir(f"e_{episode:06d}") / "audio.json").rename(
        ctx.paths.episode_dir(f"e_{episode:06d}") / "audio.json.bak"
    )
    _approve(ctx)
    assert _status(ctx, segment) == "needs_review"
    (ctx.paths.episode_dir(f"e_{episode:06d}") / "audio.json.bak").rename(
        ctx.paths.episode_dir(f"e_{episode:06d}") / "audio.json"
    )
    assert _approve(ctx).done == 0  # without --force a flagged segment is left for review
    assert _approve(ctx, force=True).done == 1
    assert _status(ctx, segment) == "approved"


def test_approve_v0_filters(ctx: Context) -> None:
    first, second = _episode(ctx), _episode(ctx)
    a = _segment(ctx, first, 10_000, 80_000)
    b = _segment(ctx, second, 10_000, 80_000)
    assert _approve(ctx, episode=first).done == 1
    assert (_status(ctx, a), _status(ctx, b)) == ("approved", "proposed")
    assert _approve(ctx, account="pm_tt").done == 0  # other owner
    assert _approve(ctx, account=ACCOUNT, limit=1).done == 1
    assert _status(ctx, b) == "approved"
