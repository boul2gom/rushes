"""`package` step and INDEX.html (task 8b): outbox/, outbox_test/, slots, guards (R3, R14, R22)."""

import dataclasses
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from conftest import REPO_CONFIG
from fakes.clock import FakeClock
from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    CREATOR_ACCOUNT,
    SERIES,
    START,
    ctx_events,
    make_context,
    make_episode,
    make_segment,
    meta_inputs,
    packable_post,
    snapshot,
    with_config,
)
from rushes.assign.assign import AssignStep
from rushes.config import load_config
from rushes.context import Context, Filters
from rushes.db.models import Post
from rushes.ids import post_key
from rushes.media.verify import check_delivery
from rushes.package import index_html
from rushes.package import outbox as outbox_module
from rushes.package.index_html import refresh_index, write_index
from rushes.package.outbox import PackageStep
from rushes.runner import StepReport, run_step
from rushes.schemas import UploadFile, read_json

TZ = ZoneInfo("America/New_York")
FILES = ["caption.txt", "cover.jpg", "upload.json", "video.mp4"]


@pytest.fixture
def ctx(tmp_path: Path, config_dir: Path, edit_yaml: Callable[..., None]) -> Iterator[Context]:
    """The example config with tom_and_jerry licensed and squeezie authorized: a post that is not
    a test may go to outbox/ (the plain `ctx` of the other modules has neither)."""
    edit_yaml(config_dir / "series" / f"{SERIES}.yaml", lambda d: d.update(licensed=True))
    edit_yaml(config_dir / "creators" / f"{CREATOR}.yaml", lambda d: d.update(authorized=True))
    context = make_context(tmp_path, config_dir, clock=FakeClock(START))
    yield context
    context.close()


@pytest.fixture
def unlicensed(ctx: Context) -> Context:
    """The same database and files, with the example config as it ships (nothing licensed)."""
    return dataclasses.replace(ctx, config=load_config(REPO_CONFIG))


def _sql(ctx: Context, statement: str) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(text(statement))


def _package(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, PackageStep(), Filters(**filters))


def _row(ctx: Context, post_id: int) -> dict[str, Any]:
    with ctx.engine.connect() as conn:
        found = conn.execute(text("SELECT * FROM posts WHERE id=:i"), {"i": post_id})
        return dict(found.mappings().one())


def _set(ctx: Context, post_id: int, **columns: Any) -> None:
    assignments = ", ".join(f"{name}=:{name}" for name in columns)
    with ctx.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE posts SET {assignments} WHERE id=:id"), {**columns, "id": post_id}
        )


def _folder(ctx: Context, post_id: int, account: str = ACCOUNT, *, test: bool = False) -> Path:
    return ctx.paths.post_dir(account, post_key(post_id), test=test)


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def _messages(ctx: Context) -> list[str]:
    return [str(e.get("message", "")) for e in ctx_events(ctx) if e.get("step") == "package"]


def _planned(ctx: Context, post_id: int) -> datetime:
    return datetime.fromisoformat(_row(ctx, post_id)["planned_at"])


def test_package_writes_the_folder_and_moves_the_post_to_ready(
    ctx: Context, delivery: Path
) -> None:
    hooks = ["Who wins?", "He did NOT see it"]
    post_id = packable_post(ctx, delivery, hook="Wait for the ending 😭", alternatives=hooks)
    report = _package(ctx)
    assert (report.done, report.failed, report.skipped) == (1, 0, 0), _messages(ctx)
    row = _row(ctx, post_id)
    assert row["status"] == "ready"
    folder = _folder(ctx, post_id)
    assert _names(folder) == [
        "caption.txt", "cover.jpg", "hook.txt", "hook_2.txt", "hook_3.txt",
        "upload.json", "video.mp4",
    ]  # fmt: skip
    assert (folder / "caption.txt").read_text(encoding="utf-8") == row["caption"]
    assert (folder / "hook.txt").read_text(encoding="utf-8") == "Wait for the ending 😭"
    assert (folder / "hook_3.txt").read_text(encoding="utf-8") == "He did NOT see it"
    assert check_delivery(folder / "video.mp4") == []
    upload = read_json(UploadFile, folder / "upload.json")
    assert upload.post_id == post_key(post_id) and upload.account == "@saturdaytoons"
    # the suggested slot: UTC in the row, shown in New York and Paris time in upload.json
    slot = _planned(ctx, post_id)
    assert slot.tzinfo is not None and slot.utcoffset() == timedelta(0)
    assert upload.publish_at.brand_local.startswith(f"{slot.astimezone(TZ):%Y-%m-%d %H:%M}")
    assert slot > ctx.clock.now() and slot.astimezone(TZ).date() == datetime(2026, 11, 17).date()
    assert not [p for p in folder.iterdir() if p.name.startswith(".")]  # no temporary


def test_package_hook_files_exist_only_for_hooks_that_exist(ctx: Context, delivery: Path) -> None:
    none = packable_post(ctx, delivery)
    one = packable_post(ctx, delivery, hook="Only one", alternatives=["Second"])
    _package(ctx)
    assert _names(_folder(ctx, none)) == FILES
    assert _names(_folder(ctx, one)) == [*FILES[:2], "hook.txt", "hook_2.txt", *FILES[2:]]


def test_package_video_is_a_hard_link_when_the_volume_allows(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    _package(ctx)
    render = ctx.paths.renders / f"{post_key(post_id)}.mp4"
    assert os.path.samefile(render, _folder(ctx, post_id) / "video.mp4")


def test_package_copies_when_a_link_is_impossible(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(source: Any, target: Any) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(outbox_module.os, "link", refused)
    post_id = packable_post(ctx, delivery)
    assert _package(ctx).done == 1
    render = ctx.paths.renders / f"{post_key(post_id)}.mp4"
    copy = _folder(ctx, post_id) / "video.mp4"
    assert copy.read_bytes() == render.read_bytes() and not os.path.samefile(render, copy)


def test_package_replaces_an_older_outbox_file_without_touching_the_render(
    ctx: Context, delivery: Path
) -> None:
    post_id = packable_post(ctx, delivery)
    render = ctx.paths.renders / f"{post_key(post_id)}.mp4"
    before = render.read_bytes()
    folder = _folder(ctx, post_id)
    folder.mkdir(parents=True)
    old = folder / "video.mp4"
    os.link(render, old)  # an earlier attempt left a link to the render
    (folder / "caption.txt").write_text("old", encoding="utf-8")
    assert _package(ctx).done == 1
    assert render.read_bytes() == before and old.read_bytes() == before
    assert (folder / "caption.txt").read_text(encoding="utf-8") != "old"


def test_package_removes_a_hook_file_that_no_longer_applies(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    folder = _folder(ctx, post_id)
    folder.mkdir(parents=True)
    (folder / "hook_3.txt").write_text("left over", encoding="utf-8")
    _package(ctx)
    assert _names(folder) == FILES


def test_package_unicode_caption_and_hook_are_written_as_utf8(ctx: Context, delivery: Path) -> None:
    caption = "Ça y est 😭 « Tom » 日本語 #fyp"
    post_id = packable_post(ctx, delivery, caption=caption, hashtags=["#fyp"], hook="Ça 😭")
    _package(ctx)
    folder = _folder(ctx, post_id)
    assert (folder / "caption.txt").read_bytes() == caption.encode("utf-8")
    raw = (folder / "upload.json").read_text(encoding="utf-8")
    assert "Ça y est 😭 « Tom » 日本語" in raw and "\\u" not in raw


# --- R14, R22: where a post goes -----------------------------------------------------------------


def _assigned(ctx: Context, delivery: Path, *, owner: str, creator: str | None = None) -> int:
    """A post made by the real `assign` step (`is_test` comes from the licence), then rendered."""
    with Session(ctx.engine) as session:
        episode = make_episode(session, owner=owner, creator_id=creator)
        make_segment(session, episode, "part", 0, 60_000)
        session.commit()
    assert run_step(ctx, AssignStep(), Filters()).done == 1
    with ctx.engine.connect() as conn:
        post_id = int(conn.execute(text("SELECT max(id) FROM posts")).scalar_one())
    _set(ctx, post_id, status="rendered", render_version="plate-1+ov-1", part_number=1)
    meta_inputs(ctx, post_id, delivery)
    return post_id


def test_r14_unlicensed_series_goes_to_outbox_test(unlicensed: Context, delivery: Path) -> None:
    post_id = _assigned(unlicensed, delivery, owner=ACCOUNT)
    assert _row(unlicensed, post_id)["is_test"] == 1  # tom_and_jerry: licensed false
    assert _package(unlicensed).done == 1
    folder = _folder(unlicensed, post_id, test=True)
    assert _names(folder) == FILES
    assert not unlicensed.paths.outbox.exists()  # nothing at all in outbox/
    upload = read_json(UploadFile, folder / "upload.json")
    assert upload.checklist[0] == "NE PAS PUBLIER : série sans licence (vitrine)"
    assert _row(unlicensed, post_id)["status"] == "ready"


def test_r14_licensed_series_goes_to_outbox(ctx: Context, delivery: Path) -> None:
    post_id = _assigned(ctx, delivery, owner=ACCOUNT)
    assert _row(ctx, post_id)["is_test"] == 0
    assert _package(ctx).done == 1
    assert _names(_folder(ctx, post_id)) == FILES and not ctx.paths.outbox_test.exists()


def test_r22_unauthorized_creator_goes_to_outbox_test(unlicensed: Context, delivery: Path) -> None:
    post_id = _assigned(unlicensed, delivery, owner=CREATOR_ACCOUNT, creator=CREATOR)
    assert _package(unlicensed).done == 1
    folder = _folder(unlicensed, post_id, CREATOR_ACCOUNT, test=True)
    assert _names(folder) == FILES and not unlicensed.paths.outbox.exists()
    upload = read_json(UploadFile, folder / "upload.json")
    assert upload.checklist[0] == "NE PAS PUBLIER : créateur non autorisé"


def test_r22_authorized_creator_goes_to_outbox(ctx: Context, delivery: Path) -> None:
    post_id = _assigned(ctx, delivery, owner=CREATOR_ACCOUNT, creator=CREATOR)
    assert _package(ctx).done == 1
    assert _names(_folder(ctx, post_id, CREATOR_ACCOUNT)) == FILES


def _nothing_packed(ctx: Context, post_id: int, account: str = ACCOUNT) -> None:
    assert _row(ctx, post_id)["status"] == "rendered" and _row(ctx, post_id)["planned_at"] is None
    assert not _folder(ctx, post_id, account).exists()
    assert not ctx.paths.outbox.exists() and not ctx.paths.outbox_test.exists()


def test_r14_licence_revoked_after_assign_is_not_packaged_in_outbox(
    ctx: Context, unlicensed: Context, delivery: Path
) -> None:
    post_id = _assigned(ctx, delivery, owner=ACCOUNT)  # licensed when assigned: is_test 0
    report = _package(unlicensed)  # the licence was taken back since
    assert (report.done, report.failed) == (0, 1)
    message = " ".join(_messages(unlicensed))
    assert "R14" in message and "pas de paquet dans outbox/" in message
    assert "rétablir `licensed`/`authorized` dans la config" in message  # the way out today
    _nothing_packed(ctx, post_id)


def test_r22_authorization_revoked_after_assign_is_not_packaged_in_outbox(
    ctx: Context, unlicensed: Context, delivery: Path
) -> None:
    post_id = _assigned(ctx, delivery, owner=CREATOR_ACCOUNT, creator=CREATOR)
    report = _package(unlicensed)
    assert (report.done, report.failed) == (0, 1)
    assert "R22" in " ".join(_messages(unlicensed))
    _nothing_packed(ctx, post_id, CREATOR_ACCOUNT)


def test_r14_licence_revoked_during_the_work_is_not_packaged_in_outbox(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = _assigned(ctx, delivery, owner=ACCOUNT)
    # a prospect series is a test whatever the config says: it lands between the read and the lock
    _during_check(
        monkeypatch, lambda: _sql(ctx, f"UPDATE series SET origin='prospect' WHERE id='{SERIES}'")
    )
    outcome = PackageStep().run(ctx, post_id, force=False)
    assert outcome.status == "failed" and "R14" in outcome.message
    _nothing_packed(ctx, post_id)


def test_r14_test_post_whose_licence_came_since_stays_in_outbox_test(
    ctx: Context, unlicensed: Context, delivery: Path
) -> None:
    post_id = _assigned(unlicensed, delivery, owner=ACCOUNT)  # a showcase post
    assert _package(ctx).done == 1  # the licence came: re-assigning is for the queue (16b, 40a)
    assert _names(_folder(ctx, post_id, test=True)) == FILES and not ctx.paths.outbox.exists()


# --- what is (not) packaged ----------------------------------------------------------------------


def test_r3_unapproved_never_packaged(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, segment_status="needs_review")
    for force in (False, True):
        report = _package(ctx, force=force)
        assert (report.done, report.failed) == (0, 0)
        assert PackageStep().select(ctx, Filters(force=force)) == []  # not even taken up
    assert _row(ctx, post_id)["status"] == "rendered"
    assert not ctx.paths.outbox.exists() and not ctx.paths.outbox_test.exists()
    # the step itself refuses too, whoever called it
    outcome = PackageStep().run(ctx, post_id, force=True)
    assert outcome.status == "skipped" and "non approuvé" in outcome.message


def test_package_only_takes_rendered_posts_with_a_finished_render_and_meta_files(
    ctx: Context, delivery: Path
) -> None:
    planned = packable_post(ctx, delivery, status="planned")
    reset = packable_post(ctx, delivery)  # render_version reset: waits for its re-render
    _set(ctx, reset, render_version=None)
    no_caption = packable_post(ctx, delivery)
    _set(ctx, no_caption, caption=None)
    no_meta = packable_post(ctx, delivery, write_meta=False)
    ok = packable_post(ctx, delivery)
    for force in (False, True):
        assert PackageStep().select(ctx, Filters(force=force)) == [ok]
    assert _package(ctx, force=True).done == 1
    for other in (planned, reset, no_caption, no_meta):
        assert _row(ctx, other)["status"] in ("planned", "rendered")
        assert not _folder(ctx, other).exists()


def test_package_never_repackages_a_ready_post_even_with_force(
    ctx: Context, delivery: Path
) -> None:
    post_id = packable_post(ctx, delivery)
    _package(ctx)
    after_first = snapshot(ctx)
    for force in (False, True):
        report = _package(ctx, force=force)
        assert (report.done, report.skipped, report.failed) == (0, 0, 0)
    assert snapshot(ctx) == after_first
    assert PackageStep().run(ctx, post_id, force=True).status == "skipped"


def test_package_refuses_non_conforming_video(ctx: Context, delivery: Path, synth: Any) -> None:
    from fixtures.generator import SynthSpec

    post_id = packable_post(ctx, delivery)
    bad = synth(SynthSpec(duration_ms=2000, size=(320, 180), fps=10))
    (ctx.paths.renders / f"{post_key(post_id)}.mp4").write_bytes(bad.read_bytes())
    report = _package(ctx)
    assert (report.done, report.failed) == (0, 1)
    message = " ".join(_messages(ctx))
    assert "non conforme à R7" in message and "1080x1920" in message
    assert _row(ctx, post_id)["status"] == "rendered" and _row(ctx, post_id)["planned_at"] is None
    assert not _folder(ctx, post_id).exists()


def test_package_missing_render_fails_with_the_way_out(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, video=False)
    report = _package(ctx)
    assert report.failed == 1 and "render --force" in " ".join(_messages(ctx))
    assert _row(ctx, post_id)["status"] == "rendered"


def test_package_meta_that_disagrees_with_the_row_fails_and_writes_nothing(
    ctx: Context, delivery: Path
) -> None:
    post_id = packable_post(ctx, delivery)
    _set(ctx, post_id, caption="a caption written after the meta file")
    assert _package(ctx).failed == 1
    assert not _folder(ctx, post_id).exists() and _row(ctx, post_id)["status"] == "rendered"


# --- slots ---------------------------------------------------------------------------------------


def test_package_gives_parts_slots_in_the_order_of_their_number(
    ctx: Context, delivery: Path
) -> None:
    # ids are in the reverse order of the numbers: the number decides
    with Session(ctx.engine) as session:
        episode = make_episode(session).id
        session.commit()
    third = packable_post(ctx, delivery, episode=episode, part_number=3, span=(120_000, 180_000))
    second = packable_post(ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000))
    first = packable_post(ctx, delivery, episode=episode, part_number=1, span=(0, 60_000))
    assert PackageStep().select(ctx, Filters()) == [first, second, third]
    assert _package(ctx).done == 3
    slots = [_planned(ctx, p) for p in (first, second, third)]
    assert slots == sorted(slots) and len(set(slots)) == 3
    assert len({s.astimezone(TZ).date() for s in slots}) == 3  # ppd 1: one per local day
    note = read_json(UploadFile, _folder(ctx, third) / "upload.json").order_note
    assert note == f"Publier après la partie 2 ({post_key(second)})"
    assert read_json(UploadFile, _folder(ctx, first) / "upload.json").order_note is None


def test_package_second_part_waits_for_the_first_to_have_a_slot(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, write_meta=False)  # meta not made yet
    with ctx.engine.connect() as conn:
        episode = int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())
    second = packable_post(ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000))
    report = _package(ctx)
    assert (report.done, report.skipped) == (0, 1)
    assert "attend la partie 1" in " ".join(_messages(ctx)) and post_key(first) in _messages(ctx)[0]
    assert _row(ctx, second)["status"] == "rendered" and not _folder(ctx, second).exists()
    meta_inputs(ctx, first, delivery)  # now the first one is ready to go
    assert _package(ctx).done == 2
    assert _planned(ctx, first) < _planned(ctx, second)


def test_package_slots_do_not_collide_between_posts_of_one_account(
    ctx: Context, delivery: Path
) -> None:
    posts = [packable_post(ctx, delivery) for _ in range(3)]
    _package(ctx)
    days = [_planned(ctx, p).astimezone(TZ).date() for p in posts]
    assert len(set(days)) == 3  # ppd 1 on TikTok: one a day
    other = packable_post(ctx, delivery, owner=ACCOUNT_SAME_BRAND)  # Instagram, 0.6 a day
    _package(ctx)
    assert _row(ctx, other)["status"] == "ready"


def test_package_a_discarded_post_frees_its_slot(ctx: Context, delivery: Path) -> None:
    first = packable_post(ctx, delivery)
    _package(ctx)
    slot = _row(ctx, first)["planned_at"]
    _set(ctx, first, status="discarded")
    second = packable_post(ctx, delivery)
    _package(ctx)
    assert _row(ctx, second)["planned_at"] == slot


def test_package_a_part_never_comes_before_the_slot_of_the_part_before_it(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, status="ready")
    far = "2026-12-24T20:00:00+00:00"  # Louis scheduled part 1 far ahead
    _set(ctx, first, planned_at=far)
    with ctx.engine.connect() as conn:
        episode = int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())
    second = packable_post(ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000))
    assert _package(ctx).done == 1
    assert _planned(ctx, second) > datetime.fromisoformat(far)


def test_package_a_slot_moved_during_the_work_discards_the_files(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = packable_post(ctx, delivery)
    _set(ctx, post_id, planned_at="2026-12-24T20:00:00+00:00")  # a slot it already holds
    _during_check(monkeypatch, lambda: _set(ctx, post_id, planned_at="2026-12-25T20:00:00+00:00"))
    outcome = PackageStep().run(ctx, post_id, force=False)
    assert outcome.status == "skipped" and "fichiers jetés" in outcome.message
    assert _row(ctx, post_id)["status"] == "rendered"
    assert _row(ctx, post_id)["planned_at"] == "2026-12-25T20:00:00+00:00"


def test_package_keeps_the_slot_a_post_already_has(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    held = "2026-12-24T20:00:00+00:00"
    _set(ctx, post_id, planned_at=held)
    _package(ctx)
    assert _row(ctx, post_id)["planned_at"] == held and _row(ctx, post_id)["status"] == "ready"
    upload = read_json(UploadFile, _folder(ctx, post_id) / "upload.json")
    assert upload.publish_at.brand_local == "2026-12-24 15:00 America/New_York"


def test_package_no_free_slot_is_reported_for_the_post(
    ctx: Context, delivery: Path, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d.update(posts_per_day={"tiktok": 0, "instagram": 0.6}),
    )
    post_id = packable_post(ctx, delivery)
    report = _package(with_config(ctx, config_dir))
    assert report.failed == 1 and _row(ctx, post_id)["status"] == "rendered"


# --- idempotence, crash and concurrent changes ---------------------------------------------------


def test_r16_package_rerun_changes_nothing(ctx: Context, delivery: Path) -> None:
    for _ in range(2):
        packable_post(ctx, delivery)
    packable_post(ctx, delivery, owner=CREATOR_ACCOUNT, creator_id=CREATOR, test=True)
    _package(ctx)
    ctx.clock.advance(days=1)  # type: ignore[attr-defined]
    before = snapshot(ctx)
    again = _package(ctx)
    assert (again.done, again.skipped, again.failed) == (0, 0, 0)
    assert snapshot(ctx) == before


def test_package_crash_between_renames_and_commit_converges(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = packable_post(ctx, delivery)
    real = outbox_module.Staged.commit
    calls = {"n": 0}

    def commit_then_die(self: Any) -> None:
        real(self)
        calls["n"] += 1
        raise RuntimeError("killed after the renames")

    monkeypatch.setattr(outbox_module.Staged, "commit", commit_then_die)
    assert _package(ctx).failed == 1
    assert _row(ctx, post_id)["status"] == "rendered" and _names(_folder(ctx, post_id)) == FILES
    monkeypatch.setattr(outbox_module.Staged, "commit", real)
    assert _package(ctx).done == 1  # the next run redoes it
    assert _row(ctx, post_id)["status"] == "ready" and _names(_folder(ctx, post_id)) == FILES


def _during_check(monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]) -> None:
    """Run `change` once the files are being built (after check_delivery of the render)."""
    real = outbox_module.check_delivery

    def spy(path: Path) -> list[str]:
        change()
        return real(path)

    monkeypatch.setattr(outbox_module, "check_delivery", spy)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("UPDATE posts SET status='discarded' WHERE id={i}", id="discarded"),
        pytest.param("UPDATE posts SET caption='changed' WHERE id={i}", id="caption"),
        pytest.param("UPDATE posts SET render_version=NULL WHERE id={i}", id="render-reset"),
        pytest.param("UPDATE posts SET part_number=2 WHERE id={i}", id="renumbered"),
        pytest.param("UPDATE posts SET is_test=1 WHERE id={i}", id="licence-flip"),
        pytest.param("UPDATE posts SET planned_at='2026-12-01T20:00' WHERE id={i}", id="slot"),
        pytest.param(
            "UPDATE segments SET status='rejected' "
            "WHERE id=(SELECT segment_id FROM posts WHERE id={i})",
            id="rejected",
        ),
    ],
)  # fmt: skip
def test_package_a_change_during_the_work_discards_the_files(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    post_id = packable_post(ctx, delivery)

    def mutate() -> None:
        with ctx.engine.begin() as conn:
            conn.execute(text(change.format(i=post_id)))

    _during_check(monkeypatch, mutate)
    report = _package(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0), _messages(ctx)
    assert "fichiers jetés" in " ".join(_messages(ctx))
    assert _row(ctx, post_id)["status"] != "ready"
    for root in (ctx.paths.outbox, ctx.paths.outbox_test):  # no file, no empty folder
        assert not list(root.rglob("*")) if root.exists() else True
    assert not [p for p in ctx.paths.data_dir.rglob(".*tmp*")]


def test_package_a_slot_taken_during_the_work_discards_the_files(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = packable_post(ctx, delivery)
    rival = packable_post(ctx, delivery)
    free = outbox_module._next_slot

    def mutate() -> None:  # a concurrent packager gives the rival the slot `mine` is about to use
        with Session(ctx.engine) as session:
            slot = free(ctx, session, session.get_one(Post, mine), after=None)
        _set(ctx, rival, planned_at=slot.isoformat(timespec="seconds"))

    _during_check(monkeypatch, mutate)
    outcome = PackageStep().run(ctx, mine, force=False)
    assert outcome.status == "skipped" and "fichiers jetés" in outcome.message
    assert _row(ctx, mine)["status"] == "rendered" and not _folder(ctx, mine).exists()


# --- INDEX.html ----------------------------------------------------------------------------------


def _index(ctx: Context, *, test: bool = False) -> str:
    folder = ctx.paths.outbox_test if test else ctx.paths.outbox
    return (folder / "INDEX.html").read_text(encoding="utf-8")


def test_index_html_lists_posts_by_account_in_order(ctx: Context, delivery: Path) -> None:
    late = packable_post(ctx, delivery, caption="Later post #fyp", hashtags=["#fyp"])
    early = packable_post(
        ctx, delivery, caption="Earlier <b>post</b> & more #fyp", hashtags=["#fyp"]
    )
    instagram = packable_post(
        ctx, delivery, owner=ACCOUNT_SAME_BRAND, caption="Reel", hashtags=["#cartoons"]
    )
    _set(ctx, late, planned_at="2026-11-20T20:00:00+00:00")
    _set(ctx, early, planned_at="2026-11-18T20:00:00+00:00")
    for post_id in (late, early, instagram):
        _set(ctx, post_id, is_test=0)
    _package(ctx)
    page = _index(ctx)
    assert page.index("@saturdaytoons") < page.index(post_key(early)) < page.index(post_key(late))
    assert page.index("st_ig") < page.index("st_tt")  # sections follow the account id
    assert page.index(post_key(instagram)) < page.index(post_key(early))
    # relative links, no server, no script
    assert f'href="st_tt/{post_key(early)}/video.mp4"' in page
    assert f'src="st_tt/{post_key(early)}/cover.jpg"' in page
    assert "<script" not in page.lower() and "http://" not in page and "https://" not in page
    assert page.count("<pre>") >= 6  # caption and hashtags of every post
    # caption text is escaped (autoescape)
    assert "Earlier &lt;b&gt;post&lt;/b&gt; &amp; more #fyp" in page and "<b>post</b>" not in page


def test_index_html_escapes_everything_a_caption_can_carry(ctx: Context, delivery: Path) -> None:
    evil = '</pre><script>alert("x")</script>'
    post_id = packable_post(
        ctx, delivery, caption=evil, hashtags=["#fyp"], hook='"><img src=x onerror=1>'
    )
    _set(ctx, post_id, is_test=0)
    _package(ctx)
    page = _index(ctx)
    assert "<script" not in page and "<img src=x" not in page
    assert "&lt;/pre&gt;&lt;script&gt;" in page


def test_index_html_separates_the_test_page_and_warns(ctx: Context, delivery: Path) -> None:
    live = packable_post(ctx, delivery)
    _set(ctx, live, is_test=0)
    showcase = packable_post(ctx, delivery, test=True)
    _package(ctx)
    assert post_key(live) in _index(ctx) and post_key(showcase) not in _index(ctx)
    page = _index(ctx, test=True)
    assert post_key(showcase) in page and post_key(live) not in page
    assert "NE PAS PUBLIER" in page and "NE PAS PUBLIER" not in _index(ctx)


def test_index_html_rerun_changes_no_byte_and_follows_departures(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, test=True)
    second = packable_post(ctx, delivery, test=True)
    _package(ctx)
    path = ctx.paths.outbox_test / "INDEX.html"
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    ctx.clock.advance(days=2)  # type: ignore[attr-defined]
    assert write_index(ctx, test=True) == path
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime  # not even rewritten
    _set(ctx, second, status="discarded")
    assert post_key(second) in path.read_text(encoding="utf-8")
    write_index(ctx, test=True)
    page = path.read_text(encoding="utf-8")
    assert post_key(first) in page and post_key(second) not in page


def test_index_html_is_written_only_when_there_is_something_to_list(
    ctx: Context, delivery: Path
) -> None:
    assert _package(ctx).done == 0
    assert not ctx.paths.outbox.exists() and not ctx.paths.outbox_test.exists()
    post_id = packable_post(ctx, delivery, test=True)
    _package(ctx)
    assert (ctx.paths.outbox_test / "INDEX.html").is_file()
    assert not (ctx.paths.outbox / "INDEX.html").exists()
    _set(ctx, post_id, status="discarded")
    assert refresh_index(ctx, test=True) is not None  # the old page must stop listing it
    _package(ctx)
    assert "Aucun post" in _index(ctx, test=True)


def test_index_html_shows_a_post_whose_upload_json_is_gone(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    _package(ctx)
    (_folder(ctx, post_id, test=False) / "upload.json").unlink()  # is_test is 0 by default here
    write_index(ctx, test=False)
    assert "illisible" in _index(ctx)


def test_package_the_slot_a_post_holds_is_free_for_itself(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    _package(ctx)
    held = _planned(ctx, post_id)
    with Session(ctx.engine) as session:
        post = session.get_one(Post, post_id)
        assert outbox_module._next_slot(ctx, session, post, after=None) == held


def test_index_html_orders_posts_by_time_not_by_text(ctx: Context, delivery: Path) -> None:
    a, b, c = (packable_post(ctx, delivery, test=True) for _ in range(3))
    _package(ctx)
    # as text: "2026-11-20T09:00+02:00" < "2026-11-20T08:30+00:00" is False although it is earlier
    _set(ctx, a, planned_at="2026-11-20T08:30:00+00:00")  # 08:30 UTC
    _set(ctx, b, planned_at="2026-11-20T09:00:00+02:00")  # 07:00 UTC, text after a's
    _set(ctx, c, planned_at="2026-11-19T23:00")  # no offset (UTC), the earliest
    write_index(ctx, test=True)
    page = _index(ctx, test=True)
    assert page.index(post_key(c)) < page.index(post_key(b)) < page.index(post_key(a))


def test_index_html_finish_builds_each_page_once(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packable_post(ctx, delivery)
    packable_post(ctx, delivery, test=True)
    calls: list[bool] = []
    real = index_html.build_sections

    def spy(context: Context, *, test: bool) -> Any:
        calls.append(test)
        return real(context, test=test)

    monkeypatch.setattr(index_html, "build_sections", spy)
    _package(ctx)
    assert sorted(calls) == [False, True]  # one query pass per folder, not two


def _episode_of(ctx: Context) -> int:
    with ctx.engine.connect() as conn:
        return int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())


def test_package_a_rejected_first_part_does_not_block_the_next(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, segment_status="rejected", write_meta=False)
    second = packable_post(
        ctx, delivery, episode=_episode_of(ctx), part_number=2, span=(60_000, 120_000)
    )
    for _ in range(2):
        _package(ctx)
    assert _row(ctx, second)["status"] == "ready" and _row(ctx, first)["status"] == "rendered"
    assert read_json(UploadFile, _folder(ctx, second) / "upload.json").order_note is None


def test_package_a_gap_in_the_numbers_still_follows_the_earlier_slot(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, status="ready")
    far = "2026-12-24T20:00:00+00:00"
    _set(ctx, first, planned_at=far)
    episode = _episode_of(ctx)
    middle = packable_post(ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000))
    _set(ctx, middle, status="discarded")  # R9: the number of the third is not renumbered yet
    third = packable_post(ctx, delivery, episode=episode, part_number=3, span=(120_000, 180_000))
    assert _package(ctx).done == 1
    assert _planned(ctx, third) > datetime.fromisoformat(far)
    note = read_json(UploadFile, _folder(ctx, third) / "upload.json").order_note
    assert note == f"Publier après la partie 1 ({post_key(first)})"


def test_package_a_part_follows_the_latest_slot_of_all_the_earlier_parts(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, status="ready")
    episode = _episode_of(ctx)
    second = packable_post(
        ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000), status="ready"
    )
    _set(ctx, first, planned_at="2026-12-24T20:00:00+00:00")  # part 1 is the latest
    _set(ctx, second, planned_at="2026-11-30T20:00:00+00:00")
    third = packable_post(ctx, delivery, episode=episode, part_number=3, span=(120_000, 180_000))
    _package(ctx)
    assert _planned(ctx, third) > datetime.fromisoformat("2026-12-24T20:00:00+00:00")


def test_package_an_error_while_building_leaves_no_empty_folder(
    ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = packable_post(ctx, delivery)
    cover = ctx.paths.renders / f"{post_key(post_id)}.cover.jpg"
    _during_check(monkeypatch, cover.unlink)  # the cover is gone when the files are staged
    report = _package(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert _row(ctx, post_id)["status"] == "rendered"
    assert not ctx.paths.outbox.exists() and not ctx.paths.outbox_test.exists()


def test_package_waits_for_the_nearest_earlier_part_not_for_the_first(
    ctx: Context, delivery: Path
) -> None:
    first = packable_post(ctx, delivery, part_number=1, status="ready")
    _set(ctx, first, planned_at="2026-11-20T20:00:00+00:00")
    episode = _episode_of(ctx)
    second = packable_post(
        ctx, delivery, episode=episode, part_number=2, span=(60_000, 120_000), write_meta=False
    )
    third = packable_post(ctx, delivery, episode=episode, part_number=3, span=(120_000, 180_000))
    report = _package(ctx)
    assert (report.done, report.skipped) == (0, 1)
    assert (
        post_key(second) in " ".join(_messages(ctx)) and "attend la partie 2" in _messages(ctx)[0]
    )
    assert _row(ctx, third)["status"] == "rendered"


def test_package_a_revoked_post_builds_nothing(
    ctx: Context, unlicensed: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assigned(ctx, delivery, owner=ACCOUNT)

    calls: list[int] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    real = outbox_module.build_upload
    monkeypatch.setattr(outbox_module, "build_upload", spy)
    assert _package(unlicensed).failed == 1
    assert calls == []  # refused before any work (the lock checks again, for the late revocation)


def test_index_html_survives_a_malformed_slot_and_lists_it_last(
    ctx: Context, delivery: Path
) -> None:
    ok, broken, none = (packable_post(ctx, delivery, test=True) for _ in range(3))
    _package(ctx)
    _set(ctx, ok, planned_at="2026-12-30T20:00:00+00:00")
    _set(ctx, broken, planned_at="not a date")
    _set(ctx, none, planned_at=None)
    assert write_index(ctx, test=True).is_file()  # does not raise
    page = _index(ctx, test=True)
    assert page.index(post_key(ok)) < page.index(post_key(broken))
    assert page.index(post_key(ok)) < page.index(post_key(none))
    entry = page[page.index(f'id="{post_key(broken)}"') :]
    assert "illisible" in entry.split("</article>")[0]
    ctx.paths.outbox_test.joinpath("INDEX.html").unlink()
    refresh_index(ctx, test=True)  # and finish() does not raise either
    assert _package(ctx).failed == 0
