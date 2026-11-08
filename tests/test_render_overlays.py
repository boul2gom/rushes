"""`render` step with the overlays pass (task 7b): banner, hook, bar, end card, light re-render."""

import dataclasses
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.orm import Session

from fixtures.generator import Synth, SynthSpec
from helpers import (
    ACCOUNT,
    ACCOUNT_OTHER_BRAND,
    CREATOR,
    CREATOR_ACCOUNT,
    ctx_events,
    ctx_output,
    make_episode,
    make_post,
    make_segment,
    post_on_video,
    snapshot,
)
from rushes.config import Layout, RenderSettings
from rushes.context import Context, Filters
from rushes.db.models import Episode, Hook, Post, Segment, Series
from rushes.errors import MediaError, RushesError
from rushes.fsutil import Staged
from rushes.ids import episode_key
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe
from rushes.media.verify import check_delivery
from rushes.paths import Paths
from rushes.render import overlays
from rushes.render import render as render_module
from rushes.render.canvas import Rect
from rushes.render.charter import load_charter
from rushes.render.encode import read_plate_tag
from rushes.render.job import build_job
from rushes.render.overlays import RENDER_OVERLAY_VERSION, render_overlays
from rushes.render.render import RENDER_PLATE_VERSION, RENDER_VERSION, RenderStep, plate_tag
from rushes.runner import StepReport, run_step

SOURCE = SynthSpec(duration_ms=6000)
FAST = RenderSettings(preset="ultrafast", crf=30)
SPAN = (1000, 5000)  # 4 s: banner and hook first, the end card from 2 s
SHORT = (500, 2500)  # 2 s: the end card covers the whole video


@pytest.fixture
def fast(ctx: Context) -> Context:
    settings = ctx.config.settings.model_copy(update={"render": FAST})
    return dataclasses.replace(ctx, config=dataclasses.replace(ctx.config, settings=settings))


def _render(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, RenderStep(), Filters(**filters))


def _post(ctx: Context, post_id: int) -> dict[str, Any]:
    with ctx.engine.connect() as conn:
        return dict(
            conn.execute(text("SELECT * FROM posts WHERE id=:i"), {"i": post_id}).mappings().one()
        )


def _tag(ctx: Context, post_id: int, layout: Layout, span: tuple[int, int]) -> str:
    """The tag a plate of this post must carry (episode and clean.mp4 included)."""
    with ctx.engine.connect() as conn:
        query = (
            "SELECT s.episode_id FROM posts p JOIN segments s ON s.id=p.segment_id WHERE p.id=:i"
        )
        episode = int(conn.execute(text(query), {"i": post_id}).scalar_one())
    return plate_tag(
        layout, span, ctx.paths.episode_dir(episode_key(episode)) / "clean.mp4", episode
    )


def _plate(ctx: Context, post_id: int) -> Path:
    return ctx.paths.renders / f"p_{post_id:06d}.plate.mp4"


def _final(ctx: Context, post_id: int) -> Path:
    return ctx.paths.renders / f"p_{post_id:06d}.mp4"


def _frame(path: Path, tmp_path: Path, t_s: float) -> NDArray[np.int16]:
    raw = tmp_path / f"frame-{path.stem}-{t_s}.rgb"
    run_ffmpeg(
        [
            "-ss",
            f"{t_s:.3f}",
            "-i",
            path,
            "-an",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            raw,
        ]
    )
    return np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def _diff(a: NDArray[np.int16], b: NDArray[np.int16], rect: Rect) -> float:
    """Share (in %) of the pixels of `rect` that differ clearly between two frames (the re-encode
    alone moves pixels by a few levels, never by 40)."""
    zone = (slice(rect.y, rect.y + rect.h), slice(rect.x, rect.x + rect.w))
    return float((np.abs(a[zone] - b[zone]).max(axis=2) > 40).mean() * 100)


RESET = "UPDATE posts SET render_version=NULL WHERE id=:i"
BANNER = Rect(60, 230, 880, 160)
HOOK = Rect(60, 460, 880, 200)
CARD = Rect(140, 650, 800, 620)  # the end card on the square zone


def _hook(ctx: Context, post_id: int, words: str = "Wait for the end") -> None:
    with Session(ctx.engine) as session:
        post = session.get_one(Post, post_id)
        hook = Hook(segment_id=post.segment_id, text=words, type="curiosity", engine="rules")
        session.add(hook)
        session.flush()
        post.hook_id = hook.id
        session.commit()


# --- what the final video shows --------------------------------------------------------------


def test_r9_final_video_shows_banner_hook_and_end_card_over_the_plate(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SPAN)
    _hook(fast, post_id)
    assert _render(fast).done == 1, ctx_output(fast)
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    assert check_delivery(final) == []
    assert abs(probe(final).duration_ms - probe(plate).duration_ms) <= 100

    early_f, early_p = _frame(final, tmp_path, 1.0), _frame(plate, tmp_path, 1.0)
    late_f, late_p = _frame(final, tmp_path, 3.0), _frame(plate, tmp_path, 3.0)
    quiet = _diff(early_f, early_p, Rect(0, 1600, 1080, 300))  # nothing is drawn there: the noise
    assert quiet < 2
    assert _diff(early_f, early_p, BANNER) > 20  # banner from the start
    assert _diff(late_f, late_p, BANNER) > 20  # and it stays
    assert _diff(early_f, early_p, HOOK) > _diff(late_f, late_p, HOOK) + 2  # hook, then gone
    assert _diff(late_f, late_p, CARD) > 20  # end card in the last 2 s
    assert _diff(early_f, early_p, CARD) < _diff(late_f, late_p, CARD) / 4  # not before


def test_the_progress_bar_grows_under_the_picture(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SPAN)
    assert _render(fast).done == 1
    final = _final(fast, post_id)
    colour = np.array([0xFF, 0x6B, 0x35])  # saturday_toons progress

    def lit(t_s: float) -> int:
        row = _frame(final, tmp_path, t_s)[1506]  # 12 px bar under the 420-1500 picture
        return int((np.abs(row - colour).max(axis=1) < 40).sum())

    first, second, third = lit(0.5), lit(2.0), lit(3.5)
    assert first < second < third
    assert abs(second - 540) < 60 and abs(third - 945) < 60  # t / 4 s of the 1080 px


def test_the_end_card_is_over_the_whole_video_when_it_lasts_two_seconds(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    for t in (0.2, 1.5):
        assert _diff(_frame(final, tmp_path, t), _frame(plate, tmp_path, t), CARD) > 20


def test_overlay_pass_keeps_the_plate_sound_and_the_delivery(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    assert check_delivery(plate) == [] and check_delivery(final) == []
    assert probe(final).has_audio and probe(final).acodec == "aac"
    assert read_plate_tag(plate) == _tag(fast, post_id, "square", SHORT)
    assert read_plate_tag(final) is None  # the final does not inherit the plate's tag
    row = _post(fast, post_id)
    assert row["render_version"] == RENDER_VERSION == f"{RENDER_PLATE_VERSION}+ov-1"
    assert RENDER_OVERLAY_VERSION == "ov-1"


def test_the_part_number_is_recorded_at_render(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=(0, 2000))
    assert _post(fast, post_id)["part_number"] is None
    assert _render(fast).done == 1
    assert _post(fast, post_id)["part_number"] == 1


# --- light re-render -------------------------------------------------------------------------


def test_light_rerender_reuses_plate(fast: Context, synth: Synth, tmp_path: Path) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SPAN)
    assert _render(fast).done == 1
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    plate_bytes, plate_stamp = plate.read_bytes(), plate.stat().st_mtime_ns
    final_bytes = final.read_bytes()
    before = _frame(final, tmp_path, 1.0)

    _hook(fast, post_id, "Nouvelle accroche")  # a new hook: whoever sets it resets the version
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    report = _render(fast)
    assert (report.done, report.failed) == (1, 0), ctx_output(fast)
    done = [e for e in ctx_events(fast) if e.get("status") == "done"][-1]
    assert "incrustations seules" in done["message"]

    assert plate.read_bytes() == plate_bytes  # same sha256, same file
    assert plate.stat().st_mtime_ns == plate_stamp
    assert final.read_bytes() != final_bytes  # the overlays changed
    assert _diff(_frame(final, tmp_path, 1.0), before, HOOK) > 5
    assert check_delivery(final) == []
    assert _post(fast, post_id)["render_version"] == RENDER_VERSION
    before_run = snapshot(fast)
    assert _render(fast).done == 0 and snapshot(fast) == before_run  # converged


def test_a_plate_of_another_layout_is_fully_rerendered(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT, layout="square")
    assert _render(fast).done == 1
    plate = _plate(fast, post_id)
    old = plate.read_bytes()
    with fast.engine.begin() as conn:
        conn.execute(
            text("UPDATE posts SET layout='portrait', render_version=NULL WHERE id=:i"),
            {"i": post_id},
        )
    assert _render(fast).done == 1
    assert plate.read_bytes() != old
    assert read_plate_tag(plate) == _tag(fast, post_id, "portrait", SHORT)


def test_a_plate_of_an_older_version_is_fully_rerendered(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    with monkeypatch.context() as patch:  # a render made by an older version of the plate
        patch.setattr(render_module, "RENDER_PLATE_VERSION", "plate-0")
        patch.setattr(render_module, "RENDER_VERSION", "plate-0+ov-1")
        assert _render(fast).done == 1
        assert _post(fast, post_id)["render_version"] == "plate-0+ov-1"
    plate = _plate(fast, post_id)
    old_tag = read_plate_tag(plate)
    assert old_tag is not None and "plate-0" in old_tag
    done_before = len(ctx_events(fast))
    assert _render(fast).done == 1  # version differs: selected, and the plate is not reusable
    assert read_plate_tag(plate) == _tag(fast, post_id, "square", SHORT)
    assert "incrustations seules" not in str(ctx_events(fast)[done_before:])


def test_an_overlay_version_bump_redoes_only_the_overlays(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate_bytes = _plate(fast, post_id).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(render_module, "RENDER_VERSION", f"{RENDER_PLATE_VERSION}+ov-2")
        assert _render(fast).done == 1
        assert _post(fast, post_id)["render_version"] == f"{RENDER_PLATE_VERSION}+ov-2"
    assert _plate(fast, post_id).read_bytes() == plate_bytes


def test_an_unreadable_plate_is_never_taken_for_a_current_one(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    _plate(fast, post_id).write_bytes(b"not a video")
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    assert _render(fast).done == 1
    assert check_delivery(_plate(fast, post_id)) == []


# --- C46: --force and the two files ------------------------------------------------------------


def test_force_second_rename_failing_converges_to_a_matching_pair(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corner left by C45 (8): a forced render whose second rename fails must not leave a
    new plate under an old final with nothing to repair it."""
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    old_final = final.read_bytes()
    row = _post(fast, post_id)
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == final:
            raise OSError("disque plein")
        real(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        report = _render(fast, force=True)
    assert (report.done, report.failed) == (0, 1)
    assert _post(fast, post_id) == row  # rolled back: same version, same path
    assert final.read_bytes() == old_final  # the old final, under...
    assert plate.stat().st_mtime_ns > final.stat().st_mtime_ns  # ...a newer plate
    assert [p.name for p in fast.paths.renders.iterdir() if p.name.startswith(".")] == []

    assert RenderStep().select(fast, Filters()) == [post_id]  # the version is current, yet selected
    new_plate = plate.read_bytes()
    assert _render(fast).done == 1  # the final is rebuilt from the plate on disk
    assert plate.read_bytes() == new_plate  # the plate is not rendered a third time
    assert final.stat().st_mtime_ns > plate.stat().st_mtime_ns
    assert check_delivery(final) == []
    assert _render(fast).done == 0
    assert RenderStep().select(fast, Filters()) == []


def test_a_missing_final_is_rebuilt_even_at_the_current_version(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate_bytes = _plate(fast, post_id).read_bytes()
    _final(fast, post_id).unlink()
    assert _render(fast).done == 1
    assert check_delivery(_final(fast, post_id)) == []
    assert _plate(fast, post_id).read_bytes() == plate_bytes  # light path
    assert _render(fast).done == 0


def test_a_missing_plate_is_rebuilt_even_at_the_current_version(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    _plate(fast, post_id).unlink()
    assert _render(fast).done == 1
    assert check_delivery(_plate(fast, post_id)) == []
    assert _render(fast).done == 0


def test_force_renders_the_plate_again_too(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    stamp = _plate(fast, post_id).stat().st_mtime_ns
    assert _render(fast, force=True).done == 1
    assert _plate(fast, post_id).stat().st_mtime_ns > stamp
    assert _final(fast, post_id).stat().st_mtime_ns > _plate(fast, post_id).stat().st_mtime_ns


def test_crash_between_the_renames_and_the_commit_converges_with_overlays(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    real = Staged.commit

    def commit_then_die(self: Staged) -> None:
        real(self)
        raise RuntimeError("tué entre le renommage et la validation")

    with monkeypatch.context() as patch:
        patch.setattr(Staged, "commit", commit_then_die)
        assert _render(fast).failed == 1
    assert _post(fast, post_id)["status"] == "planned"
    assert _render(fast).done == 1  # the valid plate left by the crash is reused
    assert check_delivery(_final(fast, post_id)) == []
    assert _render(fast).done == 0


# --- idempotence, paths, errors ----------------------------------------------------------------


def test_r16_render_with_overlays_rerun_changes_nothing(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    _hook(fast, post_id)
    assert _render(fast).done == 1
    before = snapshot(fast)
    stamps = [p.stat().st_mtime_ns for p in (_plate(fast, post_id), _final(fast, post_id))]
    events = len(ctx_events(fast))
    report = _render(fast)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(fast) == before
    assert len(ctx_events(fast)) == events
    assert stamps == [p.stat().st_mtime_ns for p in (_plate(fast, post_id), _final(fast, post_id))]
    assert list(fast.paths.tmp.iterdir()) == []  # the overlay PNGs of the run are gone


def test_render_works_with_unicode_path(fast: Context, synth: Synth, tmp_path: Path) -> None:
    paths = Paths(config_dir=fast.paths.config_dir, data_dir=tmp_path / "data ü dir")
    ctx = dataclasses.replace(fast, paths=paths)
    post_id = post_on_video(ctx, synth(SOURCE), span=SHORT)
    _hook(ctx, post_id, "Accroche à vérifier ✨")
    assert _render(ctx).done == 1, ctx_output(ctx)
    assert check_delivery(_final(ctx, post_id)) == []
    assert "data ü dir" in str(_final(ctx, post_id))


def test_an_advanced_module_post_fails_with_a_clear_message_until_task_35(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT, owner="tt_tt")  # advanced in config
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    failure = next(e for e in ctx_events(fast) if e.get("status") == "failed")
    assert "advanced" in failure["message"] and "tâche 35" in failure["message"]
    assert "originality: base" in failure["message"]  # the way out is in the message
    assert _post(fast, post_id)["status"] == "planned"
    assert not fast.paths.renders.exists() or list(fast.paths.renders.iterdir()) == []


def test_the_stored_post_module_wins_over_the_config(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT, owner="tt_tt")
    with fast.engine.begin() as conn:  # the post was assigned with `base` (task 8a)
        conn.execute(text("UPDATE posts SET originality='base' WHERE id=:i"), {"i": post_id})
    assert _render(fast).done == 1


# --- the overlays pass itself (arguments, no encode) ----------------------------------------


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(args: Any, **kwargs: Any) -> str:
        calls.append([str(a) for a in args])
        return ""

    monkeypatch.setattr(overlays, "run_ffmpeg", fake)
    return calls


def _plan(tmp_path: Path, *, duration: int = 5000, bar: bool = True) -> Any:
    from PIL import Image

    from rushes.originality.base import Overlay, RenderPlan

    png = tmp_path / "x.png"
    Image.new("RGBA", (10, 10)).save(png)
    items = [Overlay(png, 60, 230, 0, duration), Overlay(png, 140, 700, duration - 2000, duration)]
    return RenderPlan(items, [], bar, False)


def test_overlay_pass_feeds_pngs_as_inputs_and_never_uses_a_text_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)
    render_overlays(
        tmp_path / "plate.mp4", _plan(tmp_path), tmp_path / "out.mp4", duration_ms=5000,
        bar_rect=Rect(0, 1500, 1080, 12), bar_color="#FF6B35", s=FAST,
    )  # fmt: skip
    args = calls[0]
    joined = " ".join(args)
    assert "drawtext" not in joined and "subtitles" not in joined and "ass=" not in joined
    graph = args[args.index("-filter_complex") + 1]
    assert args.count("-loop") == 2 and str(tmp_path / "x.png") in args
    assert "overlay=x=60:y=230:enable='between(t,0.000,5.000)'" in graph
    assert "enable='between(t,3.000,5.000)'" in graph
    assert "overlay=x='-W+W*t/5.000':y=1500" in graph  # the duration is a plain number
    assert "color=c=0xFF6B35:s=1080x12:r=30:d=5.000" in joined
    assert args[args.index("-c:a") + 1] == "copy"  # the plate's sound is kept as it is
    assert args[-1] == str(tmp_path / "out.mp4")


def test_overlay_pass_without_bar_has_no_color_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)
    render_overlays(
        tmp_path / "plate.mp4", _plan(tmp_path, bar=False), tmp_path / "out.mp4",
        duration_ms=5000, bar_rect=Rect(0, 1500, 1080, 12), bar_color="#FF6B35", s=FAST,
    )  # fmt: skip
    assert "color=c=" not in " ".join(calls[0])


@pytest.mark.parametrize("colour", ["red", "#FFF", "#GGGGGG", "FF6B35"])
def test_overlay_pass_refuses_a_bad_bar_colour(tmp_path: Path, colour: str) -> None:
    with pytest.raises(ValueError, match="couleur"):
        render_overlays(
            tmp_path / "p.mp4", _plan(tmp_path), tmp_path / "o.mp4", duration_ms=5000,
            bar_rect=Rect(0, 1500, 1080, 12), bar_color=colour, s=FAST,
        )  # fmt: skip


def test_overlay_pass_refuses_audio_layers_until_task_35(tmp_path: Path) -> None:
    from rushes.originality.base import AudioLayer

    plan = dataclasses.replace(
        _plan(tmp_path), audio_layers=[AudioLayer(tmp_path / "v.wav", 0, -12)]
    )
    with pytest.raises(NotImplementedError, match="tâche 35"):
        render_overlays(
            tmp_path / "p.mp4", plan, tmp_path / "o.mp4", duration_ms=5000,
            bar_rect=Rect(0, 1500, 1080, 12), bar_color="#FF6B35", s=FAST,
        )  # fmt: skip


# --- the job built from the database -----------------------------------------------------------


def _job(ctx: Context, post_id: int) -> Any:
    with Session(ctx.engine) as session:
        return build_job(
            ctx, session, post_id, layout="square", duration_ms=30_000, src_w=1280, src_h=720
        )


def _part(session: Session, episode: Episode, i: int) -> Segment:
    return make_segment(session, episode, "part", i * 10_000, i * 10_000 + 9_000)


def _parts_for(ctx: Context, owner: str = ACCOUNT, n: int = 3) -> list[int]:
    with Session(ctx.engine) as session:
        episode = make_episode(session, owner=owner)
        posts = [
            make_post(
                session,
                make_segment(session, episode, "part", i * 10_000, i * 10_000 + 9_000),
                owner,
            )
            for i in range(n)
        ]
        session.commit()
        return [p.id for p in posts]


def test_job_numbers_the_parts_and_announces_the_next_one(ctx: Context) -> None:
    ids = _parts_for(ctx)
    first, second, third = (_job(ctx, i) for i in ids)
    assert [j.part_number for j in (first, second, third)] == [1, 2, 3]
    assert (first.next_label, second.next_label) == ("Part 2 next", "Part 3 next")
    assert [j.is_last_part for j in (first, second, third)] == [False, False, True]
    assert first.title == "Tom and Jerry" and first.handle == "@saturdaytoons"
    assert first.language == "en" and first.bestof_title is None and first.hook_text is None
    assert first.avatar == ctx.paths.config_dir / "avatars" / "saturday_toons.png"
    assert first.charter == load_charter(ctx.paths.config_dir)["saturday_toons"]


def test_job_last_part_names_another_active_series_of_the_account(ctx: Context) -> None:
    ids = _parts_for(ctx, n=2)
    with Session(ctx.engine) as session:  # the account also owns an episode of another series
        make_episode(session, series_id="oggy")
        session.commit()
    last = _job(ctx, ids[1])
    assert last.is_last_part and last.next_label == "Watch Oggy and the Cockroaches next"
    assert _job(ctx, ids[1]).next_label == last.next_label  # seeded on the post: reproducible


def test_job_other_series_draw_is_seeded_on_the_post(ctx: Context) -> None:
    with Session(ctx.engine) as session:
        for sid in ("a", "b", "c", "d", "e"):
            session.add(Series(id=sid, title=f"Serie {sid}", audio="nonverbal", licensed=False))
        session.flush()
        for sid in ("a", "b", "c", "d", "e"):
            make_episode(session, series_id=sid)
        session.commit()
    seen = set()
    for _ in range(8):
        episode_ids = _parts_for(ctx, n=1)  # a lone part is the last one
        first = _job(ctx, episode_ids[0]).next_label
        assert first == _job(ctx, episode_ids[0]).next_label
        seen.add(first)
    assert len(seen) > 1  # the draw depends on the post, not a fixed choice
    labels = {f"Watch Serie {sid} next" for sid in "abcde"}
    assert seen <= labels  # only the account's other series: never the one of the post
    assert not any("Tom and Jerry" in label for label in seen)


def test_job_last_part_without_another_series_says_follow(ctx: Context) -> None:
    (only,) = _parts_for(ctx, n=1)
    job = _job(ctx, only)
    assert job.is_last_part and job.next_label == "Follow for more"
    with Session(ctx.engine) as session:  # another series, but dropped: still nothing to point to
        make_episode(session, series_id="oggy")
        session.execute(text("UPDATE series SET status='dropped' WHERE id='oggy'"))
        session.commit()
    assert _job(ctx, only).next_label == "Follow for more"


def test_job_in_french_for_a_creator_brand(ctx: Context) -> None:
    with Session(ctx.engine) as session:
        episode = make_episode(session, owner=CREATOR_ACCOUNT, series_id=None, creator_id=CREATOR)
        post = make_post(session, make_segment(session, episode, "part", 0, 9_000), CREATOR_ACCOUNT)
        other = make_episode(session, owner=CREATOR_ACCOUNT, series_id=None, creator_id="byilhan")
        assert other.id
        session.commit()
        post_id = post.id
    job = _job(ctx, post_id)
    assert (job.title, job.language, job.handle) == ("Squeezie", "fr", "@instantculte")
    assert job.is_last_part and job.next_label == "À voir ensuite : Byilhan"


def test_job_takes_the_hook_of_the_post(ctx: Context) -> None:
    (post_id,) = _parts_for(ctx, n=1)
    assert _job(ctx, post_id).hook_text is None
    _hook(ctx, post_id, "Regarde bien la fin")
    assert _job(ctx, post_id).hook_text == "Regarde bien la fin"


def test_job_of_a_non_part_post_is_a_titled_bestof(ctx: Context) -> None:
    with Session(ctx.engine) as session:
        episode = make_episode(session)
        post = make_post(session, make_segment(session, episode, "bestof_item", 0, 9_000))
        session.commit()
        post_id = post.id
    job = _job(ctx, post_id)
    assert job.part_number is None and job.bestof_title == "Tom and Jerry"
    assert job.next_label is None and not job.is_last_part


def test_job_of_another_brand_uses_its_own_charter(ctx: Context) -> None:
    (post_id,) = _parts_for(ctx, n=1, owner=ACCOUNT_OTHER_BRAND)
    job = _job(ctx, post_id)
    assert job.charter.banner_bg == "#1E1B2E" and job.handle == "@toontape"  # toon_tape, not st
    assert job.avatar.name == "toon_tape.png"


def test_job_refuses_a_blank_title(ctx: Context) -> None:
    (post_id,) = _parts_for(ctx, n=1)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE series SET title='  ' WHERE id='tom_and_jerry'"))
    with pytest.raises(RushesError, match="titre vide"):
        _job(ctx, post_id)


# --- review fixes: compare-and-swap, charter before the plate, plate source, damaged plate ----


def _versions(ctx: Context, post_id: int) -> tuple[str | None, int | None]:
    row = _post(ctx, post_id)
    return row["render_version"], row["hook_id"]


def _after_overlays(monkeypatch: pytest.MonkeyPatch, act: Any) -> None:
    """`act()` runs once, right after the overlays pass wrote its temporary (another writer)."""
    real = render_module.render_overlays
    done: list[int] = []

    def wrapped(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        if not done:
            done.append(1)
            act()

    monkeypatch.setattr(render_module, "render_overlays", wrapped)


@pytest.mark.parametrize("reset", ["version", "hook"])
def test_a_reset_during_the_overlays_pass_is_not_overwritten(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch, reset: str
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    _hook(fast, post_id, "Old hook")
    with fast.engine.begin() as conn:  # a first signal: the post is to redo
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    final = _final(fast, post_id)
    old_final = final.read_bytes()
    jobs: list[str | None] = []
    real_build = render_module.build_job

    def spy(*args: Any, **kwargs: Any) -> Any:
        job = real_build(*args, **kwargs)
        jobs.append(job.hook_text)
        return job

    def later_writer() -> None:  # the same signal again, with a new hook, mid-render
        _hook(fast, post_id, "New hook")
        if reset == "version":
            with fast.engine.begin() as conn:
                conn.execute(
                    text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id}
                )

    with monkeypatch.context() as patch:
        patch.setattr(render_module, "build_job", spy)
        _after_overlays(patch, later_writer)
        report = _render(fast)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0), ctx_output(fast)
    skipped = next(e for e in ctx_events(fast) if e.get("status") == "skipped")
    assert "n'est plus à rendre" in skipped["message"]
    assert _post(fast, post_id)["render_version"] is None  # the signal survived
    assert final.read_bytes() == old_final  # nothing replaced, no temporary left
    assert [p.name for p in fast.paths.renders.iterdir() if p.name.startswith(".")] == []

    with monkeypatch.context() as patch:
        patch.setattr(render_module, "build_job", spy)
        assert _render(fast).done == 1  # the next run takes the new hook
    assert jobs == ["Old hook", "New hook"]
    assert _post(fast, post_id)["render_version"] == RENDER_VERSION
    assert _render(fast).done == 0


def test_a_damaged_avatar_costs_no_plate(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The charter and the plan come before the plate: no 1 to 3 minute encode to throw away."""
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    (fast.paths.config_dir / "avatars" / "saturday_toons.png").write_bytes(b"damaged")
    called: list[int] = []
    real = render_module.render_plate

    def spy(*args: Any, **kwargs: Any) -> Any:
        called.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(render_module, "render_plate", spy)
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    failure = next(e for e in ctx_events(fast) if e.get("status") == "failed")
    assert "saturday_toons.png" in failure["message"] and "illisible" in failure["message"]
    assert called == [] and not list(fast.paths.renders.glob("*"))
    assert _post(fast, post_id)["status"] == "planned"


def test_a_re_cleaned_source_never_reuses_the_old_plate(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate = _plate(fast, post_id)
    old_tag, old_stamp = read_plate_tag(plate), plate.stat().st_mtime_ns
    clean = fast.paths.episodes / "e_000001" / "clean.mp4"
    stat = clean.stat()
    os.utime(clean, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # clean --force
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    assert _render(fast).done == 1
    assert plate.stat().st_mtime_ns > old_stamp  # rendered again, not reused
    assert read_plate_tag(plate) != old_tag
    assert read_plate_tag(plate) == _tag(fast, post_id, "square", SHORT)
    assert "incrustations seules" not in str(ctx_events(fast)[-1])


def test_a_kept_plate_that_turns_out_unusable_is_rendered_again_once(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plate cut short after its header still carries the right tag: the light path fails, the
    post is then redone in full instead of failing on every run."""
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    plate = _plate(fast, post_id)
    stamp = plate.stat().st_mtime_ns
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    real = render_module.render_overlays
    attempts: list[Path] = []

    def flaky(plate_in: Path, *args: Any, **kwargs: Any) -> None:
        attempts.append(plate_in)
        if plate_in == plate:  # the kept plate
            raise MediaError("ffmpeg a échoué (code 1)\nmoov atom not found")
        real(plate_in, *args, **kwargs)

    monkeypatch.setattr(render_module, "render_overlays", flaky)
    report = _render(fast)
    assert (report.done, report.failed) == (1, 0), ctx_output(fast)
    assert len(attempts) == 2 and attempts[0] == plate and attempts[1] != plate
    assert plate.stat().st_mtime_ns > stamp  # the plate was rendered again
    done = [e for e in ctx_events(fast) if e.get("status") == "done"][-1]
    assert "incrustations seules" not in done["message"]
    assert any("rendue de nouveau" in str(e) for e in ctx_events(fast))
    assert check_delivery(_final(fast, post_id)) == []


def test_a_failing_full_render_after_the_light_one_still_fails_the_post(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL WHERE id=:i"), {"i": post_id})
    calls: list[int] = []

    def broken(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        raise MediaError("ffmpeg a échoué")

    monkeypatch.setattr(render_module, "render_overlays", broken)
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    assert len(calls) == 2  # light once, full once: no loop
    assert _post(fast, post_id)["render_version"] is None


# --- N1: each guard of the compare-and-swap UPDATE, alone ------------------------------------

OLD_OVERLAYS = f"{RENDER_PLATE_VERSION}+ov-0"  # a row whose overlays are one version behind


def _set(sql: str) -> Any:
    def act(ctx: Context, post_id: int) -> None:
        with ctx.engine.begin() as conn:
            conn.execute(text(sql), {"i": post_id})

    return act


def _new_hook(ctx: Context, post_id: int) -> None:
    _hook(ctx, post_id, "Another hook")


RELAYOUT = "UPDATE posts SET layout='portrait' WHERE id=:i"
DISCARD = "UPDATE posts SET status='discarded' WHERE id=:i"
REJECT = "UPDATE segments SET status='rejected' WHERE id=(SELECT segment_id FROM posts WHERE id=:i)"
# guard -> (what another writer does during the render, how the row shows it survived)
CAS_GUARDS: dict[str, tuple[Any, Any]] = {
    "render_version": (_set(RESET), lambda row: row["render_version"] is None),
    "hook_id": (_new_hook, lambda row: row["hook_id"] is not None),
    "layout": (_set(RELAYOUT), lambda row: row["layout"] == "portrait"),
    "status": (_set(DISCARD), lambda row: row["status"] == "discarded"),
    "segment": (_set(REJECT), lambda row: row["render_version"] == OLD_OVERLAYS),
}  # fmt: skip


@pytest.mark.parametrize("guard", list(CAS_GUARDS))
def test_each_guard_of_the_final_update_holds_on_its_own(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    """Only one thing changes during the overlays pass: the UPDATE must match nothing. Removing
    that guard from the UPDATE makes exactly its case fail."""
    act, survived = CAS_GUARDS[guard]
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    with fast.engine.begin() as conn:  # one overlay version behind: a light re-render is due
        conn.execute(
            text("UPDATE posts SET render_version=:v WHERE id=:i"),
            {"v": OLD_OVERLAYS, "i": post_id},
        )
    final = _final(fast, post_id)
    old_final = final.read_bytes()
    _after_overlays(monkeypatch, lambda: act(fast, post_id))
    report = _render(fast)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0), ctx_output(fast)
    skipped = next(e for e in ctx_events(fast) if e.get("status") == "skipped")
    assert "n'est plus à rendre" in skipped["message"]
    row = _post(fast, post_id)
    assert row["render_version"] != RENDER_VERSION  # the render did not mark the post done
    assert survived(row)  # the other writer's change was not overwritten
    assert final.read_bytes() == old_final
    assert [p.name for p in fast.paths.renders.iterdir() if p.name.startswith(".")] == []
