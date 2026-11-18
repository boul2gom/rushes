import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fixtures.generator import Synth, SynthSpec
from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    CREATOR_ACCOUNT_SAME_BRAND,
    SERIES,
    age_file,
    ctx_events,
    drop_import,
    make_episode,
    make_segment,
    with_config,
)
from rushes.context import Context, Filters
from rushes.db.models import Episode, Post
from rushes.errors import RushesError
from rushes.ingest.ingest import IngestStep
from rushes.ingest.owner import choose_owner
from rushes.runner import StepReport, run_step

TINY = SynthSpec(duration_ms=3000, size=(64, 36))
TINY_B = SynthSpec(duration_ms=3500, size=(64, 36))
TODAY = date(2026, 11, 16)  # first day of the rotation of config/accounts.yaml
EditYaml = Callable[[Path, Callable[[Any], Any]], None]


def _ingest(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, IngestStep(), Filters(**filters))


def _episodes(ctx: Context) -> list[Episode]:
    with Session(ctx.engine) as db:
        return list(db.scalars(select(Episode).order_by(Episode.id)))


def _step_events(ctx: Context) -> list[dict[str, Any]]:
    return [e for e in ctx_events(ctx) if e.get("event") == "step"]


def _no_rotation(config_dir: Path, edit_yaml: EditYaml) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d.pop("rotation"))


# --- IngestStep -------------------------------------------------------------------------------


def test_ingest_copies_hashes_and_assigns_owner(ctx: Context, synth: Synth) -> None:
    video = synth(TINY)
    original = drop_import(ctx, SERIES, video, "a.MP4")
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (1, 0, 0, 0)

    (episode,) = _episodes(ctx)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    assert episode.sha256 == digest
    assert episode.file_path == f"sources/local_{digest[:16]}.mp4"  # relative to data/
    assert (ctx.paths.data_dir / episode.file_path).read_bytes() == video.read_bytes()
    assert (episode.provenance, episode.status) == ("local", "imported")
    assert episode.source_video_id is None
    assert (episode.series_id, episode.creator_id) == (SERIES, None)
    assert abs(episode.duration_ms - 3000) <= 60
    # the rotation of config/ gives tom_and_jerry to saturday_toons on 2026-11-16: smallest id
    assert episode.owner_account_id == ACCOUNT_SAME_BRAND
    # the original has moved to import/_done/
    assert not original.exists()
    assert (ctx.paths.imports / "_done" / "a.MP4").read_bytes() == video.read_bytes()


def test_ingest_creator_folder_makes_a_creator_episode(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, CREATOR, synth(TINY))
    assert _ingest(ctx).done == 1
    (episode,) = _episodes(ctx)
    assert (episode.series_id, episode.creator_id) == (None, CREATOR)
    assert episode.owner_account_id == CREATOR_ACCOUNT_SAME_BRAND  # instant_culte, smallest id


def test_ingest_twice_skips_same_sha(ctx: Context, synth: Synth) -> None:
    video = synth(TINY)
    drop_import(ctx, SERIES, video, "a.mp4")
    assert _ingest(ctx).done == 1
    again = drop_import(ctx, SERIES, video, "a.mp4")  # same content dropped a second time
    report = _ingest(ctx)
    assert (report.done, report.skipped) == (0, 1)
    assert len(_episodes(ctx)) == 1
    assert not again.exists()
    assert len(list((ctx.paths.imports / "_done").iterdir())) == 2  # name collision kept both


def test_ingest_rerun_with_nothing_to_do_changes_nothing(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, SERIES, synth(TINY))
    _ingest(ctx)
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)


def test_ingest_unknown_folder_warns(ctx: Context, synth: Synth) -> None:
    stray = drop_import(ctx, "not_a_series", synth(TINY))
    report = _ingest(ctx)
    assert (report.done, report.warned) == (0, 1)
    assert stray.exists()  # left alone
    assert _episodes(ctx) == []
    assert "not_a_series" in _step_events(ctx)[0]["message"]


def test_ingest_ignores_underscore_dirs(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, "_done", synth(TINY), "x.mp4")
    drop_import(ctx, "_whatever", synth(TINY), "y.mp4")
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert _step_events(ctx) == []


def test_ingest_ignores_hidden_files(ctx: Context) -> None:
    hidden = ctx.paths.imports / SERIES / ".DS_Store"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"x")
    report = _ingest(ctx)
    assert (report.warned, report.failed, report.done, report.skipped) == (0, 0, 0, 0)
    assert _step_events(ctx) == []


def test_ingest_unreadable_video_fails_and_stays_in_import(ctx: Context) -> None:
    broken = ctx.paths.imports / SERIES / "broken.mp4"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a video")
    age_file(ctx, broken)
    report = _ingest(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert broken.exists() and _episodes(ctx) == []
    assert not ctx.paths.sources.exists() or not list(ctx.paths.sources.iterdir())


def test_ingest_non_video_extension_warns(ctx: Context) -> None:
    notes = ctx.paths.imports / SERIES / "notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_text("hello")
    age_file(ctx, notes)
    report = _ingest(ctx)
    assert (report.warned, report.failed) == (1, 0) and notes.exists()


def test_ingest_filters_limit_the_folders(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, SERIES, synth(TINY))
    drop_import(ctx, CREATOR, synth(TINY_B))
    assert _ingest(ctx, creator=CREATOR).done == 1
    assert [e.creator_id for e in _episodes(ctx)] == [CREATOR]
    assert _ingest(ctx, series=SERIES).done == 1


def test_ingest_resumes_after_a_crash_between_copy_and_database(ctx: Context, synth: Synth) -> None:
    video = synth(TINY)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    leftover = ctx.paths.sources / f"local_{digest[:16]}.mp4"  # copied, then the process died
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(video.read_bytes())
    drop_import(ctx, SERIES, video)
    assert _ingest(ctx).done == 1
    assert [e.sha256 for e in _episodes(ctx)] == [digest]


def test_ingest_resumes_after_a_crash_between_database_and_move(ctx: Context, synth: Synth) -> None:
    video = synth(TINY)
    drop_import(ctx, SERIES, video)
    _ingest(ctx)
    for leftover in (ctx.paths.imports / "_done").iterdir():  # the original is back in import/
        leftover.rename(ctx.paths.imports / SERIES / leftover.name)
    report = _ingest(ctx)
    assert (report.done, report.skipped) == (0, 1)
    assert len(_episodes(ctx)) == 1
    assert not list((ctx.paths.imports / SERIES).iterdir())


def test_ingest_uses_no_hard_coded_path_with_accents_and_spaces(
    tmp_path: Path, config_dir: Path, synth: Synth
) -> None:
    from fakes.clock import FakeClock
    from helpers import START, make_context

    context = make_context(tmp_path / "ü dir", config_dir, clock=FakeClock(START))
    try:
        drop_import(context, SERIES, synth(TINY), "un épisode.mp4")
        assert _ingest(context).done == 1
    finally:
        context.close()


# --- choose_owner (R4, R44) -------------------------------------------------------------------


def test_r4_new_episode_goes_to_current_rotation(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # today, tom_and_jerry belongs to toon_tape (accounts tt_ig, tt_tt), not to saturday_toons
    def swap(data: Any) -> None:
        data["rotation"] = [
            {"from": date(2026, 11, 9), "to": date(2026, 11, 29)}
            | {"saturday_toons": ["oggy"], "toon_tape": [SERIES]}
        ]

    edit_yaml(config_dir / "accounts.yaml", swap)
    context = with_config(ctx, config_dir)
    drop_import(context, SERIES, synth(TINY))
    assert _ingest(context).done == 1
    (episode,) = _episodes(context)
    assert episode.owner_account_id == "tt_ig"
    assert context.config.account(episode.owner_account_id).brand == "toon_tape"


def test_r4_period_not_covering_today_is_ignored(ctx: Context, session: Session) -> None:
    config = ctx.config
    # 2026-12-07: the second period gives tom_and_jerry to toon_tape
    assert choose_owner(
        session, config, series_id=SERIES, creator_id=None, today=date(2026, 12, 8)
    ).startswith("tt_")
    assert choose_owner(session, config, series_id=SERIES, creator_id=None, today=TODAY).startswith(
        "st_"
    )


def test_owner_without_rotation_uses_every_brand_of_the_vertical(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, session: Session
) -> None:
    _no_rotation(config_dir, edit_yaml)
    config = with_config(ctx, config_dir).config
    for account in ("st_ig", "st_tt", "tt_ig"):
        make_episode(session, owner=account)
    assert choose_owner(session, config, series_id=SERIES, creator_id=None, today=TODAY) == "tt_tt"


def test_owner_balances_stock(ctx: Context, session: Session) -> None:
    make_episode(session, owner=ACCOUNT_SAME_BRAND)
    assert (
        choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY) == ACCOUNT
    )
    make_episode(session, owner=ACCOUNT)
    make_episode(session, owner=ACCOUNT)
    assert (
        choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY)
        == ACCOUNT_SAME_BRAND
    )


def test_owner_tie_goes_to_the_smallest_id(ctx: Context, session: Session) -> None:
    owner = choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY)
    assert owner == min(ACCOUNT, ACCOUNT_SAME_BRAND)


def test_owner_stock_counts_unpublished_posts_and_uncut_episodes(
    ctx: Context, session: Session
) -> None:
    # ACCOUNT: one cut episode, all its posts published -> no stock
    done = make_episode(session, owner=ACCOUNT)
    segment = make_segment(session, done, "part", 0, 70_000)
    session.add(Post(account_id=ACCOUNT, segment_id=segment.id, status="published"))
    # ACCOUNT_SAME_BRAND: one episode not cut yet: 600 s / 90 s = 6.7 posts, 11 days at 0.6 a day
    make_episode(session, owner=ACCOUNT_SAME_BRAND)
    session.flush()
    owner = choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY)
    assert owner == ACCOUNT
    # one unpublished post: 1 day of stock for ACCOUNT, still under the 11 days of the other
    session.add(Post(account_id=ACCOUNT, segment_id=segment.id, status="ready"))
    session.flush()
    owner = choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY)
    assert owner == ACCOUNT
    # twelve more: 13 days, the other account now has the smaller stock
    session.add_all(
        Post(account_id=ACCOUNT, segment_id=segment.id, status="planned") for _ in range(12)
    )
    session.flush()
    owner = choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=TODAY)
    assert owner == ACCOUNT_SAME_BRAND


def test_owner_of_a_creator_episode_comes_from_creators_by_brand(
    ctx: Context, session: Session
) -> None:
    owner = choose_owner(session, ctx.config, series_id=None, creator_id=CREATOR, today=TODAY)
    assert ctx.config.brand_of(owner).id == "instant_culte"  # squeezie is listed there only


def test_owner_needs_exactly_one_subject(ctx: Context, session: Session) -> None:
    with pytest.raises(ValueError):
        choose_owner(session, ctx.config, series_id=None, creator_id=None, today=TODAY)
    with pytest.raises(ValueError):
        choose_owner(session, ctx.config, series_id=SERIES, creator_id=CREATOR, today=TODAY)


def test_owner_balances_within_one_run(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _no_rotation(config_dir, edit_yaml)
    context = with_config(ctx, config_dir)
    drop_import(context, SERIES, synth(TINY), "a.mp4")
    drop_import(context, SERIES, synth(TINY_B), "b.mp4")
    assert _ingest(context).done == 2
    owners = [e.owner_account_id for e in _episodes(context)]
    assert len(set(owners)) == 2, owners


def test_ingest_does_not_touch_the_registry(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, SERIES, synth(TINY))
    _ingest(ctx)
    with ctx.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM assignments")).scalar() == 0


def test_ingest_copies_when_hard_links_are_refused(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("rushes.ingest.ingest.os.link", refuse)
    video = synth(TINY)
    drop_import(ctx, SERIES, video)
    assert _ingest(ctx).done == 1
    (episode,) = _episodes(ctx)
    assert (ctx.paths.data_dir / episode.file_path).read_bytes() == video.read_bytes()


# --- review fixes: I1, M5-M8 ----------------------------------------------------------------


def test_r4_series_absent_from_current_rotation_gets_no_owner(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth, session: Session
) -> None:
    # a period covers today and gives only oggy to a brand: tom_and_jerry has no account
    def only_oggy(data: Any) -> None:
        data["rotation"] = [
            {"from": date(2026, 11, 9), "to": date(2026, 11, 29)} | {"toon_tape": ["oggy"]}
        ]

    edit_yaml(config_dir / "accounts.yaml", only_oggy)
    context = with_config(ctx, config_dir)
    with pytest.raises(RushesError) as raised:
        choose_owner(session, context.config, series_id=SERIES, creator_id=None, today=TODAY)
    assert raised.value.hint and "rotation" in raised.value.hint
    original = drop_import(context, SERIES, synth(TINY))
    report = _ingest(context)
    assert (report.done, report.failed) == (0, 1)
    assert original.exists() and _episodes(context) == []  # stays in import/, not in _done/
    assert not context.paths.import_done.exists()


def test_ingest_with_episode_or_account_filter_selects_nothing(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, SERIES, synth(TINY))
    for filters in ({"episode": 45}, {"account": ACCOUNT}):
        report = _ingest(ctx, **filters)
        assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert _episodes(ctx) == []


def test_ingest_warns_about_subfolders_but_not_underscore_ones(ctx: Context, synth: Synth) -> None:
    drop_import(ctx, f"{SERIES}/S01", synth(TINY), "ep1.mp4")
    (ctx.paths.imports / SERIES / "_old").mkdir()
    (ctx.paths.imports / SERIES / ".hidden").mkdir()
    report = _ingest(ctx)
    assert (report.done, report.warned) == (0, 1)
    message = _step_events(ctx)[0]["message"]
    assert "sous-dossier" in message and "S01" in message
    assert (ctx.paths.imports / SERIES / "S01" / "ep1.mp4").exists()


def test_ingest_skips_a_file_still_being_copied_then_takes_it_later(
    ctx: Context, synth: Synth
) -> None:
    video = synth(TINY)
    path = drop_import(ctx, SERIES, video, age_s=5)  # written 5 s ago
    report = _ingest(ctx)
    assert (report.done, report.skipped) == (0, 1)
    assert "copie en cours" in _step_events(ctx)[0]["message"]
    assert path.exists() and _episodes(ctx) == []
    age_file(ctx, path, 61)
    assert _ingest(ctx).done == 1  # skipped is not "done for good"
    assert len(_episodes(ctx)) == 1


def test_ingest_does_not_hide_a_failed_insert_as_already_imported(
    ctx: Context, synth: Synth
) -> None:
    with ctx.engine.begin() as conn:  # the series is in the config but missing from the mirror
        conn.execute(text("DELETE FROM series WHERE id=:s"), {"s": SERIES})
    original = drop_import(ctx, SERIES, synth(TINY))
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 0, 1)
    assert original.exists() and not ctx.paths.import_done.exists()
