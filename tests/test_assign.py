"""`assign` step (task 8a): approved parts go to the owner account of their episode (R1, R2, R3)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT,
    ACCOUNT_OTHER_BRAND,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    CREATOR_ACCOUNT,
    SERIES,
    assign_raw,
    ctx_events,
    make_episode,
    make_segment,
    snapshot,
    with_config,
)
from rushes.assign.assign import AssignStep, is_test_post
from rushes.context import Context, Filters
from rushes.db.models import Assignment, Episode, Post, Segment, Series
from rushes.runner import StepReport, run_step


def _assign(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, AssignStep(), Filters(**filters))


def _segment(
    ctx: Context, *, status: str = "approved", kind: str = "part", owner: str = ACCOUNT,
    start: int = 0, end: int = 70_000, creator: str | None = None, episode: int | None = None,
) -> tuple[int, int]:  # fmt: skip
    """(episode id, segment id)."""
    with Session(ctx.engine) as session:
        if episode is None:
            episode = make_episode(session, owner=owner, creator_id=creator).id
        segment = make_segment(session, session.get_one(Episode, episode), kind, start, end)
        segment.status = status
        session.commit()
        return episode, segment.id


def _count(ctx: Context, model: type) -> int:
    with Session(ctx.engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _posts(ctx: Context) -> list[Post]:
    with Session(ctx.engine) as session:
        return list(session.scalars(select(Post).order_by(Post.id)))


def _messages(ctx: Context) -> list[str]:
    return [str(e.get("message", "")) for e in ctx_events(ctx) if e.get("step") == "assign"]


def test_assign_creates_a_planned_post_for_the_owner(ctx: Context) -> None:
    _, segment = _segment(ctx)
    report = _assign(ctx)
    assert (report.done, report.warned, report.failed) == (1, 0, 0)
    [post] = _posts(ctx)
    assert (post.account_id, post.segment_id, post.status) == (ACCOUNT, segment, "planned")
    assert post.family == "monetizable"
    assert post.layout == "square"  # layouts.preference[0] of the example config
    assert post.originality == "base"
    assert post.part_number is None and post.caption is None and post.video_path is None
    with Session(ctx.engine) as session:
        assignment = session.get_one(Assignment, segment)
        assert assignment.account_id == ACCOUNT
        assert assignment.assigned_at == "2026-11-16T12:00:00+00:00"  # the context clock


def test_assign_uses_the_account_originality(ctx: Context) -> None:
    _segment(ctx, owner=ACCOUNT_OTHER_BRAND)  # tt_tt is `advanced` in the example config
    _assign(ctx)
    assert _posts(ctx)[0].originality == "advanced"


def test_assign_layout_follows_the_first_preference(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d["layouts"].update(preference=["full"]))
    _segment(ctx)
    _assign(with_config(ctx, config_dir))
    assert _posts(ctx)[0].layout == "full"


def test_assign_layout_defaults_to_square_without_preference(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d["layouts"].update(preference=[]))
    _segment(ctx)
    _assign(with_config(ctx, config_dir))
    assert _posts(ctx)[0].layout == "square"


def test_r3_unapproved_never_assigned(ctx: Context) -> None:
    for i, status in enumerate(("proposed", "needs_review", "rejected")):
        _segment(ctx, status=status, start=i * 100_000, end=i * 100_000 + 70_000)
    report = _assign(ctx, force=True)
    assert (report.done, report.warned, report.skipped, report.failed) == (0, 0, 0, 0)
    assert _count(ctx, Post) == 0 and _count(ctx, Assignment) == 0


def test_r3_segment_demoted_between_select_and_run_is_not_assigned(ctx: Context) -> None:
    _, segment = _segment(ctx)
    step = AssignStep()
    [item] = step.select(ctx, Filters())
    with Session(ctx.engine) as session:
        session.get_one(Segment, segment).status = "needs_review"
        session.commit()
    outcome = step.run(ctx, item, force=False)
    assert outcome.status == "skipped"
    assert _count(ctx, Post) == 0 and _count(ctx, Assignment) == 0


def test_r2_assign_ignores_bestof_items(ctx: Context) -> None:
    _segment(ctx, kind="bestof_item")
    _segment(ctx, kind="compilation_item", start=100_000, end=130_000)
    report = _assign(ctx, force=True)
    assert (report.done, report.warned, report.skipped, report.failed) == (0, 0, 0, 0)
    assert _count(ctx, Post) == 0 and _count(ctx, Assignment) == 0


def test_assign_refusal_is_warned_with_reason(ctx: Context) -> None:
    episode, held = _segment(ctx, start=0, end=70_000)
    _, candidate = _segment(ctx, episode=episode, start=60_000, end=130_000)  # 10 s overlap
    with Session(ctx.engine) as session:
        assign_raw(session, session.get_one(Segment, held), ACCOUNT_SAME_BRAND)
        session.commit()
    report = _assign(ctx)
    assert (report.done, report.warned, report.failed) == (0, 1, 0)
    assert any(m.startswith("refus du registre : ") and "Chevauche" in m for m in _messages(ctx))
    assert _count(ctx, Post) == 0
    assert _count(ctx, Assignment) == 1  # only the setup row; the refused segment got none
    with Session(ctx.engine) as session:
        assert session.get(Assignment, candidate) is None


def test_assign_goes_to_the_owner_only(ctx: Context) -> None:
    _segment(ctx, owner=ACCOUNT_SAME_BRAND)
    _assign(ctx)
    assert [p.account_id for p in _posts(ctx)] == [ACCOUNT_SAME_BRAND]


def test_assign_rerun_changes_nothing(ctx: Context) -> None:
    _segment(ctx)
    _segment(ctx, start=100_000, end=170_000)
    assert _assign(ctx).done == 2
    before = snapshot(ctx)
    again = _assign(ctx)
    assert (again.done, again.warned, again.skipped, again.failed) == (0, 0, 0, 0)
    assert snapshot(ctx) == before
    assert _assign(ctx, force=True).done == 0  # --force never assigns twice (R1)
    assert snapshot(ctx) == before


def test_assign_filters(ctx: Context) -> None:
    first, a = _segment(ctx)
    second, b = _segment(ctx, owner=ACCOUNT_OTHER_BRAND)
    assert _assign(ctx, account=ACCOUNT_OTHER_BRAND).done == 1
    assert [p.segment_id for p in _posts(ctx)] == [b]
    assert _assign(ctx, episode=first, limit=0).done == 0
    assert _assign(ctx, series=SERIES).done == 1
    assert [p.segment_id for p in _posts(ctx)] == [b, a]
    assert second != first


def test_assign_creator_episode_to_a_creators_account(ctx: Context) -> None:
    _segment(ctx, owner=CREATOR_ACCOUNT, creator=CREATOR)
    assert _assign(ctx, creator=CREATOR).done == 1
    [post] = _posts(ctx)
    assert (post.account_id, post.is_test) == (CREATOR_ACCOUNT, True)  # squeezie: authorized false


def test_assign_writes_post_and_assignment_in_one_transaction(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _segment(ctx)

    def boom(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("disk full")

    monkeypatch.setattr("rushes.assign.assign.is_test_post", boom)
    report = _assign(ctx)
    assert report.failed == 1
    assert _count(ctx, Post) == 0 and _count(ctx, Assignment) == 0  # nothing half-written


def test_r1_assign_uses_a_write_session(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """registry.assign refuses a plain session: the step must go through write_session."""
    _segment(ctx)
    seen: list[bool] = []
    from rushes.db import registry

    real = registry.assign

    def spy(session: Session, *args: Any, **kwargs: Any) -> Any:
        from rushes.db.engine import WRITE_LOCK

        seen.append(session.connection().info.get(WRITE_LOCK) is True)
        return real(session, *args, **kwargs)

    monkeypatch.setattr("rushes.assign.assign.assign", spy)
    assert _assign(ctx).done == 1
    assert seen == [True]


# --- is_test_post (R14, R22, R32) -------------------------------------------------------------


def _licensed(config_dir: Path, edit_yaml: Callable[..., None], series: str, value: bool) -> None:
    edit_yaml(config_dir / "series" / f"{series}.yaml", lambda d: d.update(licensed=value))


def test_r14_test_flag_for_unlicensed_series(ctx: Context) -> None:
    _segment(ctx)  # tom_and_jerry: licensed false
    _assign(ctx)
    assert _posts(ctx)[0].is_test is True


def test_r14_licensed_series_is_not_a_test(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    _licensed(config_dir, edit_yaml, SERIES, True)
    episode, _ = _segment(ctx)
    live = with_config(ctx, config_dir)
    with Session(ctx.engine) as session:
        assert is_test_post(live.config, session, episode) is False
        assert is_test_post(ctx.config, session, episode) is True
    _assign(live)
    assert _posts(ctx)[0].is_test is False


def test_r14_prospect_series_is_a_test_even_when_licensed(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    _licensed(config_dir, edit_yaml, SERIES, True)
    episode, _ = _segment(ctx)
    with Session(ctx.engine) as session:
        session.get_one(Series, SERIES).origin = "prospect"
        session.commit()
        assert is_test_post(with_config(ctx, config_dir).config, session, episode) is True


def test_r32_series_unknown_to_the_config_is_a_test(ctx: Context) -> None:
    with Session(ctx.engine) as session:
        session.add(Series(id="found_show", title="Found", audio="nonverbal", licensed=True,
                           origin="prospect", status="candidate"))  # fmt: skip
        episode = make_episode(session, series_id="found_show")
        session.commit()
        assert is_test_post(ctx.config, session, episode.id) is True


def test_r22_creator_not_authorized_is_a_test(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    episode, _ = _segment(ctx, owner=CREATOR_ACCOUNT, creator=CREATOR)
    with Session(ctx.engine) as session:
        assert is_test_post(ctx.config, session, episode) is True
    edit_yaml(config_dir / "creators" / f"{CREATOR}.yaml", lambda d: d.update(authorized=True))
    with Session(ctx.engine) as session:
        assert is_test_post(with_config(ctx, config_dir).config, session, episode) is False
