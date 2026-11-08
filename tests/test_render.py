"""`render` step (task 7a): plate on the 9:16 canvas, loudnorm, delivery check, idempotence."""

import dataclasses
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy import text

from fixtures.generator import Synth, SynthSpec
from helpers import (
    ACCOUNT,
    CREATOR,
    CREATOR_ACCOUNT,
    SERIES,
    audio_peak,
    ctx_events,
    ctx_output,
    post_on_video,
    snapshot,
)
from rushes.config import RENDER_PRESETS, RenderSettings
from rushes.context import Context, Filters
from rushes.errors import MediaError
from rushes.fsutil import Staged
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import ffprobe_json, probe
from rushes.media.verify import check_delivery
from rushes.render import encode
from rushes.render import render as render_module
from rushes.render.canvas import Rect, video_rect
from rushes.render.encode import encode_args, render_plate
from rushes.render.loudnorm import LoudnormMeasure, PlateReport
from rushes.render.render import RENDER_VERSION, RenderStep
from rushes.runner import StepReport, run_step

SOURCE = SynthSpec(duration_ms=6000)  # 1280x720, 30 i/s, tone
FAST = RenderSettings(preset="ultrafast", crf=30)
SPAN = (1000, 5000)  # 4 s
SHORT = (500, 2500)  # 2 s: enough for everything but the R7 spec test


# these tests are about the plate (picture, sound, delivery, files, rows): the overlays pass is
# tested in test_render_overlays.py, so here it is a plain copy (saves a second encode per render)
REAL_OVERLAYS = {"test_r7_render_matches_ffprobe_spec"}


@pytest.fixture(autouse=True)
def _plain_final(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.name in REAL_OVERLAYS or request.node.get_closest_marker("slow"):
        return

    def copy(plate: Path, plan: Any, out: Path, **kwargs: Any) -> None:
        shutil.copyfile(plate, out)

    monkeypatch.setattr(render_module, "render_overlays", copy)


@pytest.fixture
def fast(ctx: Context) -> Context:
    """The context with the fastest encode (the settings default is the slow `medium`)."""
    settings = ctx.config.settings.model_copy(update={"render": FAST})
    return dataclasses.replace(ctx, config=dataclasses.replace(ctx.config, settings=settings))


def _render(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, RenderStep(), Filters(**filters))


def _post(ctx: Context, post_id: int) -> dict[str, Any]:
    with ctx.engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM posts WHERE id=:i"), {"i": post_id}).mappings()
        return dict(row.one())


def _files(ctx: Context) -> list[str]:
    root = ctx.paths.renders
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def _plate(ctx: Context, post_id: int) -> Path:
    return ctx.paths.renders / f"p_{post_id:06d}.plate.mp4"


def _final(ctx: Context, post_id: int) -> Path:
    return ctx.paths.renders / f"p_{post_id:06d}.mp4"


def _episode_of(ctx: Context, post_id: int) -> int:
    with ctx.engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT s.episode_id FROM posts p JOIN segments s ON s.id=p.segment_id "
                    "WHERE p.id=:i"
                ),
                {"i": post_id},
            ).scalar_one()
        )


def _lufs(path: Path) -> float:
    """Integrated loudness of the file (ebur128 summary)."""
    log = run_ffmpeg(["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"])
    values = re.findall(r"\bI:\s+(-?\d+\.\d+)\s+LUFS", log)
    assert values, log
    return float(values[-1])


def _frame(path: Path, tmp_path: Path, t_s: float = 1.0) -> NDArray[np.uint8]:
    """One decoded 1080x1920 RGB frame (raw file under tmp_path)."""
    raw = tmp_path / "frame.rgb"
    seek = ["-ss", f"{t_s:.3f}", "-i", path, "-an", "-frames:v", "1"]
    run_ffmpeg([*seek, "-f", "rawvideo", "-pix_fmt", "rgb24", raw])
    return np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(1920, 1080, 3)


def _outlined(video: Path, tmp_path: Path) -> Path:
    """The video with a white 16 px frame at its borders: a sharp edge at every side of the
    picture, so the placement checks do not depend on where the synth's random boxes fall."""
    out = tmp_path / "outlined.mp4"
    outline = "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=16"
    run_ffmpeg(
        ["-i", video, "-vf", outline, "-c:a", "copy", "-preset", "ultrafast", "-f", "mp4", out]
    )
    return out


def _quiet_modulated(video: Path, tmp_path: Path, lufs: float = -30.0) -> Path:
    """The video's sound as steps of 1 s alternating between two levels (loudness range above 0,
    so that loudnorm's second pass can run linear), brought to `lufs` integrated."""
    steps = tmp_path / "steps.mp4"
    envelope = "volume='if(lt(mod(t,2),1),1,0.4)':eval=frame"
    aac = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    run_ffmpeg(["-i", video, "-c:v", "copy", "-af", envelope, *aac, "-f", "mp4", steps])
    quiet = tmp_path / "quiet.mp4"
    gain = f"volume={lufs - _lufs(steps):.2f}dB"
    run_ffmpeg(["-i", steps, "-c:v", "copy", "-af", gain, *aac, "-f", "mp4", quiet])
    assert abs(_lufs(quiet) - lufs) < 0.6  # the source really is at `lufs`
    return quiet


def _edge(frame: NDArray[np.uint8], y0: int, y1: int, x0: int = 0, x1: int = 1080) -> float:
    """Strongest luma step between neighbouring pixels in a zone: the blurred background has
    none (under 10 on a 0-255 scale), the video has box edges (over 20)."""
    gray = frame[y0:y1, x0:x1].astype(np.float32).mean(axis=2)
    return float(max(np.abs(np.diff(gray, axis=1)).max(), np.abs(np.diff(gray, axis=0)).max()))


BLURRED, SHARP = 10.0, 20.0


def _assert_video_at(frame: NDArray[np.uint8], rect: Rect) -> None:
    """The sharp picture fills `rect` and only `rect` (the rest is the blurred background)."""
    band = 60
    assert _edge(frame, rect.y, rect.y + band, rect.x, rect.x + rect.w) > SHARP  # starts at rect.y
    bottom = rect.y + rect.h
    assert _edge(frame, bottom - band, bottom, rect.x, rect.x + rect.w) > SHARP  # ends at its foot
    above = _edge(frame, 0, rect.y - 4)
    assert above < BLURRED, f"le fond au-dessus de la vidéo n'est pas flou ({above:.1f})"
    assert frame[: rect.y - 4].max() > 60, "fond noir au-dessus de la vidéo"
    below = _edge(frame, bottom + 4, 1920)
    assert below < BLURRED, f"le fond sous la vidéo n'est pas flou ({below:.1f})"
    if rect.x > 16:  # side bars, when the picture is narrower than the canvas
        side = _edge(frame, rect.y, bottom, 0, rect.x - 4)
        assert side < BLURRED, f"le fond sur le côté n'est pas flou ({side:.1f})"


# --- the spec: R7 -----------------------------------------------------------------------------


def test_r7_render_matches_ffprobe_spec(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SPAN)
    report = _render(fast)
    assert (report.done, report.failed) == (1, 0), ctx_output(fast)
    plate, final = _plate(fast, post_id), _final(fast, post_id)
    assert check_delivery(plate) == [] and check_delivery(final) == []
    assert abs(probe(final).duration_ms - 4000) <= 100
    row = _post(fast, post_id)
    assert (row["status"], row["render_version"]) == ("rendered", RENDER_VERSION)
    assert row["video_path"] == f"renders/p_{post_id:06d}.mp4"
    assert fast.paths.data_dir.joinpath(row["video_path"]).read_bytes() == final.read_bytes()
    assert _files(fast) == [final.name, plate.name]  # no temporary file left


def test_r7_integrated_loudness_minus14(fast: Context, synth: Synth, tmp_path: Path) -> None:
    quiet = _quiet_modulated(synth(SOURCE), tmp_path)
    post_id = post_on_video(fast, quiet, span=SPAN)
    assert _render(fast).done == 1, ctx_output(fast)
    assert abs(_lufs(_final(fast, post_id)) + 14.0) <= 1.0


def test_r7_second_pass_runs_linear_and_follows_the_measure(
    tmp_path: Path, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet = _quiet_modulated(synth(SOURCE), tmp_path)
    out = tmp_path / "plate.mp4"
    report = render_plate(quiet, SPAN, "square", out, s=FAST)
    assert report is not None and report.normalization_type == "linear"
    assert abs(_lufs(out) + 14.0) <= 1.0
    assert abs(report.output_i + 14.0) <= 1.0

    # the test is not vacuous: a measure 10 LU too loud leaves the result clearly under the target
    real = encode.measure

    def too_loud(src: Path, span: tuple[int, int]) -> LoudnormMeasure:
        m = real(src, span)
        return dataclasses.replace(m, input_i=m.input_i + 10.0)

    monkeypatch.setattr(encode, "measure", too_loud)
    wrong = tmp_path / "wrong.mp4"
    render_plate(quiet, SPAN, "square", wrong, s=FAST)
    assert _lufs(wrong) < -20.0


def test_r7_steady_tone_falls_back_to_dynamic_mode_and_still_lands_on_target(
    tmp_path: Path, synth: Synth
) -> None:
    out = tmp_path / "plate.mp4"
    report = render_plate(synth(SOURCE), SPAN, "square", out, s=FAST)
    assert report is not None and report.normalization_type == "dynamic"  # loudness range is 0
    assert abs(report.output_i + 14.0) <= 1.0 and abs(_lufs(out) + 14.0) <= 1.0


def test_render_plate_has_no_loudness_report_without_gain(tmp_path: Path, synth: Synth) -> None:
    mute = synth(SynthSpec(duration_ms=3000, silence_ms=[(0, 3000)]))
    assert render_plate(mute, (0, 2000), "square", tmp_path / "a.mp4", s=FAST) is None
    no_audio = synth(SynthSpec(duration_ms=3000, audio=False))
    assert render_plate(no_audio, (0, 2000), "square", tmp_path / "b.mp4", s=FAST) is None


def _report_loudness(monkeypatch: pytest.MonkeyPatch, output_i: float, kind: str) -> None:
    """The step sees `render_plate` report this loudness (the file itself is the real render)."""
    real = render_module.render_plate

    def wrapped(*args: Any, **kwargs: Any) -> PlateReport | None:
        real(*args, **kwargs)
        return PlateReport(output_i=output_i, normalization_type=kind)

    monkeypatch.setattr(render_module, "render_plate", wrapped)


@pytest.mark.parametrize("output_i", [-17.0, -12.5, float("nan"), float("-inf"), float("inf")])
def test_r7_loudness_outside_tolerance_fails_the_post(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch, output_i: float
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    before = snapshot(fast)
    _report_loudness(monkeypatch, output_i, "dynamic")
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    failure = next(e for e in ctx_events(fast) if e.get("status") == "failed")
    assert "-14" in failure["message"] and "R7" in failure["message"]
    assert _files(fast) == [] and _post(fast, post_id)["status"] == "planned"
    assert snapshot(fast) == before


def test_r7_dynamic_loudnorm_within_tolerance_is_done_and_says_so(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    _report_loudness(monkeypatch, -14.6, "dynamic")
    assert _render(fast).done == 1
    done = next(e for e in ctx_events(fast) if e.get("status") == "done")
    assert "loudnorm dynamique" in done["message"]
    assert _post(fast, post_id)["status"] == "rendered"


def test_r7_linear_loudnorm_is_done_without_remark(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_on_video(fast, synth(SOURCE), span=SHORT)
    _report_loudness(monkeypatch, -14.2, "linear")
    assert _render(fast).done == 1
    done = next(e for e in ctx_events(fast) if e.get("status") == "done")
    assert "dynamique" not in done["message"]


def test_check_delivery_reports_wrong_fps(tmp_path: Path, synth: Synth) -> None:
    messages = check_delivery(synth(SynthSpec(duration_ms=2000, size=(1080, 1920), fps=25)))
    assert any("30 i/s" in message for message in messages), messages
    assert not any("dimensions" in message for message in messages)


def test_check_delivery_reports_each_violated_point(tmp_path: Path, synth: Synth) -> None:
    source = synth(SynthSpec(duration_ms=2000))  # 1280x720, 30 i/s, faststart
    messages = check_delivery(source)
    assert any("1280x720" in message and "1080x1920" in message for message in messages)
    assert not any("30 i/s" in message or "faststart" in message for message in messages)
    slow_start = tmp_path / "moov-last.mp4"  # same streams, moov written after mdat
    run_ffmpeg(["-i", source, "-c", "copy", "-f", "mp4", slow_start])
    assert any("faststart" in message for message in check_delivery(slow_start))
    silent = synth(SynthSpec(duration_ms=2000, audio=False))
    assert "0 piste audio au lieu d'une" in check_delivery(silent)  # zero takes the singular


def test_check_delivery_uses_the_displayed_size(tmp_path: Path, synth: Synth) -> None:
    wide_pixels = synth(SynthSpec(duration_ms=1500, size=(1080, 1920), sar=(2, 1)))
    assert any("2160x1920" in message for message in check_delivery(wide_pixels))
    upright = synth(SynthSpec(duration_ms=1500, size=(1080, 1920)))
    turned = tmp_path / "turned.mp4"  # stored 1080x1920 but shown rotated: 1920x1080
    run_ffmpeg(["-display_rotation", "90", "-i", upright, "-c", "copy", "-f", "mp4", turned])
    assert any("1920x1080" in message for message in check_delivery(turned))
    assert not any("dimensions" in message for message in check_delivery(upright))


def test_check_delivery_names_the_wrong_ftyp_brand(tmp_path: Path, synth: Synth) -> None:
    quicktime = tmp_path / "clip.mov"
    flags = ["-movflags", "+faststart"]
    run_ffmpeg(
        ["-i", synth(SynthSpec(duration_ms=1500)), "-c", "copy", *flags, "-f", "mov", quicktime]
    )
    messages = check_delivery(quicktime)
    assert any(
        "qt" in message and "isom" in message and "marque" in message for message in messages
    )
    assert not any("mov,mp4" in message for message in messages)


def test_check_delivery_never_raises_on_a_truncated_atom(tmp_path: Path, synth: Synth) -> None:
    damaged = tmp_path / "damaged.mp4"
    good = synth(SynthSpec(duration_ms=1500)).read_bytes()
    damaged.write_bytes(good + b"\x00\x00\x00\x01free\x00\x00\x00")  # 64-bit size cut short
    assert any("atome tronqué" in message for message in check_delivery(damaged))


@pytest.mark.parametrize("preset", RENDER_PRESETS)
def test_r7_encode_args_give_the_high_profile_at_every_preset(tmp_path: Path, preset: str) -> None:
    out = tmp_path / "tiny.mp4"
    settings = RenderSettings(preset=preset, crf=30)  # type: ignore[arg-type]
    source = ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1"]
    run_ffmpeg([*source, *encode_args(settings), "-f", "mp4", out])
    streams = ffprobe_json(out)["streams"]
    assert [s["profile"] for s in streams if s["codec_type"] == "video"] == ["High"]


def test_check_delivery_of_an_unreadable_file_is_a_violation(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert len(check_delivery(broken)) == 1
    assert check_delivery(tmp_path / "absent.mp4") != []


# --- layouts on the canvas (R8) ---------------------------------------------------------------


# the placement is read on the plate: the final also carries the banner and the end card (7b)


def test_r8_square_is_default_layout(fast: Context, synth: Synth, tmp_path: Path) -> None:
    post_id = post_on_video(fast, _outlined(synth(SOURCE), tmp_path), span=SHORT, layout=None)
    assert _render(fast).done == 1
    frame = _frame(_plate(fast, post_id), tmp_path)
    _assert_video_at(frame, video_rect("square", 1280, 720))


def test_r8_empty_layout_preference_still_renders_square(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    fast.config.accounts.layouts.preference = []
    post_id = post_on_video(fast, _outlined(synth(SOURCE), tmp_path), span=SHORT)
    assert _render(fast).done == 1
    _assert_video_at(_frame(_plate(fast, post_id), tmp_path), video_rect("square", 1280, 720))


@pytest.mark.parametrize("size", [(1280, 720), (1440, 1080), (1002, 722)])
def test_r8_full_layout_puts_the_whole_image_on_a_blurred_background(
    fast: Context, synth: Synth, size: tuple[int, int], tmp_path: Path
) -> None:
    source = _outlined(synth(SynthSpec(duration_ms=3000, size=size)), tmp_path)
    post_id = post_on_video(fast, source, layout="full")
    assert _render(fast).done == 1
    assert check_delivery(_final(fast, post_id)) == []
    _assert_video_at(_frame(_plate(fast, post_id), tmp_path), video_rect("full", *size))


def test_r8_vertical_layout_fills_the_canvas(fast: Context, synth: Synth, tmp_path: Path) -> None:
    source = _outlined(synth(SOURCE), tmp_path)
    post_id = post_on_video(fast, source, span=SHORT, layout="vertical")
    assert _render(fast).done == 1
    frame = _frame(_plate(fast, post_id), tmp_path)
    # sharp picture everywhere, also where the other layouts show their blurred bars
    assert _edge(frame, 0, 400) > BLURRED and _edge(frame, 1520, 1920) > BLURRED
    assert check_delivery(_final(fast, post_id)) == []


def test_r8_portrait_layout_is_3_4_under_the_banner(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    source = _outlined(synth(SOURCE), tmp_path)
    post_id = post_on_video(fast, source, span=SHORT, layout="portrait")
    assert _render(fast).done == 1
    frame = _frame(_plate(fast, post_id), tmp_path)
    _assert_video_at(frame, video_rect("portrait", 1280, 720))


def test_r8_pixel_aspect_ratio_is_applied_before_placing(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    stored = SynthSpec(duration_ms=3000, size=(960, 720), sar=(4, 3))  # displayed 1280x720
    post_id = post_on_video(fast, _outlined(synth(stored), tmp_path), layout="full")
    assert _render(fast).done == 1
    assert check_delivery(_final(fast, post_id)) == []
    frame = _frame(_plate(fast, post_id), tmp_path)
    _assert_video_at(frame, video_rect("full", 1280, 720))  # not 4:3


# --- sound ------------------------------------------------------------------------------------


def test_r7_digital_silence_is_rendered_without_gain(fast: Context, synth: Synth) -> None:
    mute = synth(SynthSpec(duration_ms=3000, silence_ms=[(0, 3000)]))
    post_id = post_on_video(fast, mute)
    report = _render(fast)
    assert (report.done, report.failed) == (1, 0), ctx_output(fast)
    assert check_delivery(_final(fast, post_id)) == []
    assert audio_peak(_final(fast, post_id)) < 0.001  # still silent, no -inf gain applied


def test_r7_source_without_audio_gets_a_silent_aac_track(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SynthSpec(duration_ms=3000, audio=False)))
    assert _render(fast).done == 1
    assert check_delivery(_final(fast, post_id)) == []
    info = probe(_final(fast, post_id))
    assert info.acodec == "aac" and abs(info.duration_ms - 2000) <= 100


# --- frame rate -------------------------------------------------------------------------------


def test_r7_variable_frame_rate_source_comes_out_constant(fast: Context, synth: Synth) -> None:
    variable = synth(SynthSpec(duration_ms=3000, vfr=True))
    assert probe(variable).vfr
    post_id = post_on_video(fast, variable)
    assert _render(fast).done == 1
    assert not probe(_final(fast, post_id)).vfr
    assert check_delivery(_final(fast, post_id)) == []


# --- render_plate directly --------------------------------------------------------------------


def test_render_plate_reports_progress_and_accepts_a_crop_window(
    tmp_path: Path, synth: Synth
) -> None:
    seen: list[int] = []
    out = tmp_path / "plate.mp4"
    render_plate(synth(SOURCE), SHORT, "square", out, s=FAST, crop_x="0", on_progress=seen.append)
    assert check_delivery(out) == []
    assert seen and seen[-1] == 100 and seen == sorted(seen)
    assert all(isinstance(p, int) and 0 <= p <= 100 for p in seen)


def test_render_plate_bounds_the_output_to_the_span_length(
    tmp_path: Path, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    real = encode.run_ffmpeg

    def spy(args: Any, **kwargs: Any) -> str:
        seen.append([str(a) for a in args])
        return real(args, **kwargs)

    monkeypatch.setattr(encode, "run_ffmpeg", spy)
    render_plate(synth(SOURCE), SHORT, "square", tmp_path / "plate.mp4", s=FAST)
    args = seen[-1]  # the encode (the first-pass measure runs in loudnorm, not here)
    output_part = args[args.index("-map") :]  # options after the inputs and the graph
    assert "-shortest" in output_part
    assert output_part[output_part.index("-t") + 1] == "2.000"  # the span's length, as a hard limit
    assert args.index("-t") < args.index("-i")  # the input's own -t is still there, before -i


def test_render_plate_refuses_an_empty_span(tmp_path: Path, synth: Synth) -> None:
    with pytest.raises(MediaError):
        render_plate(synth(SOURCE), (1000, 1000), "square", tmp_path / "x.mp4", s=FAST)
    assert not (tmp_path / "x.mp4").exists()


# --- selection and rules ----------------------------------------------------------------------


@pytest.mark.parametrize("segment_status", ["proposed", "needs_review", "rejected"])
def test_r3_unapproved_never_rendered(fast: Context, synth: Synth, segment_status: str) -> None:
    post_id = post_on_video(fast, synth(SOURCE), segment_status=segment_status)
    before = snapshot(fast)
    for force in (False, True):
        report = _render(fast, force=force)
        assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert _files(fast) == []
    assert snapshot(fast) == before
    assert _post(fast, post_id)["status"] == "planned"


def test_r3_segment_withdrawn_after_planning_blocks_a_re_render(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), status="rendered", render_version="plate-0")
    with fast.engine.begin() as conn:
        conn.execute(text("UPDATE segments SET status='rejected'"))
    assert _render(fast).done == 0
    assert _post(fast, post_id)["render_version"] == "plate-0"


def test_version_bump_never_rerenders_ready_post(fast: Context, synth: Synth) -> None:
    video = synth(SOURCE)
    old = "plate-0"
    rendered = post_on_video(fast, video, status="rendered", render_version=old)
    ready = post_on_video(
        fast,
        video,
        span=(2500, 4500),
        status="ready",
        render_version=old,
        episode=_episode_of(fast, rendered),
    )
    for post_id in (rendered, ready):  # files of the older version
        _plate(fast, post_id).parent.mkdir(parents=True, exist_ok=True)
        _plate(fast, post_id).write_bytes(b"old plate")
        _final(fast, post_id).write_bytes(b"old final")

    def state(post_id: int) -> tuple[dict[str, Any], bytes, bytes]:
        return (
            _post(fast, post_id),
            _plate(fast, post_id).read_bytes(),
            _final(fast, post_id).read_bytes(),
        )

    ready_state = state(ready)
    for force in (False, True):
        report = _render(fast, force=force)
        assert report.failed == 0, ctx_output(fast)
        assert report.done == 1  # the rendered post only
        assert state(ready) == ready_state

    assert _post(fast, rendered)["render_version"] == RENDER_VERSION
    assert check_delivery(_final(fast, rendered)) == []  # the old bytes were replaced
    assert _post(fast, ready)["status"] == "ready" and _post(fast, ready)["render_version"] == old


def test_force_says_why_a_ready_post_is_left_alone(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), status="ready", render_version="plate-0")
    report = _render(fast, force=True)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    event = next(e for e in ctx_events(fast) if e.get("status") == "skipped")
    assert "déjà prêt" in event["message"] and event["item"] == f"p_{post_id:06d}"
    assert _files(fast) == []


def test_force_rerenders_a_planned_or_rendered_post_of_the_current_version(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    assert _render(fast).done == 0  # without --force: nothing to do
    final = _final(fast, post_id)
    final.write_bytes(b"damaged")
    assert _render(fast, force=True).done == 1
    assert check_delivery(final) == []
    assert _post(fast, post_id)["status"] == "rendered"


def test_compilation_and_discarded_posts_are_not_selected(fast: Context, synth: Synth) -> None:
    discarded = post_on_video(fast, synth(SOURCE), status="discarded")
    with fast.engine.begin() as conn:  # a compilation post: another task renders those
        compilation = conn.execute(
            text("INSERT INTO compilations(kind, account_id, status) VALUES (:k, :a, :s)"),
            {"k": "bestof", "a": ACCOUNT, "s": "proposed"},
        ).lastrowid
        conn.execute(
            text("INSERT INTO posts(account_id, compilation_id, status) VALUES (:a, :c, :s)"),
            {"a": ACCOUNT, "c": compilation, "s": "planned"},
        )
    assert _render(fast, force=True).done == 0
    assert _post(fast, discarded)["status"] == "discarded"
    assert _files(fast) == []


def test_filters_limit_the_posts_rendered(fast: Context, synth: Synth) -> None:
    video = synth(SOURCE)
    first = post_on_video(fast, video, span=SHORT)
    second = post_on_video(fast, video, span=SHORT)
    assert _render(fast, posts=(second,)).done == 1
    assert _post(fast, first)["status"] == "planned"
    assert _post(fast, second)["status"] == "rendered"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("account", ("cartoon",)),
        ("series", ("cartoon",)),
        ("creator", ("creator",)),
        ("episode", ("cartoon",)),
        ("episodes", ("creator",)),
        ("account-other", ()),
        ("posts", ("creator",)),
        ("none", ("cartoon", "creator")),
    ],
)
def test_select_honours_every_filter(
    fast: Context, synth: Synth, name: str, expected: tuple[str, ...]
) -> None:
    video = synth(SOURCE)
    posts = {
        "cartoon": post_on_video(fast, video),
        "creator": post_on_video(fast, video, owner=CREATOR_ACCOUNT, creator_id=CREATOR),
    }
    filters = {
        "account": {"account": ACCOUNT},
        "series": {"series": SERIES},
        "creator": {"creator": CREATOR},
        "episode": {"episode": _episode_of(fast, posts["cartoon"])},
        "episodes": {"episodes": (_episode_of(fast, posts["creator"]),)},
        "account-other": {"account": "tt_tt"},
        "posts": {"posts": (posts["creator"],)},
        "none": {},
    }[name]
    chosen = RenderStep().select(fast, Filters(**filters))
    assert chosen == sorted(posts[k] for k in expected)


def test_layout_falls_back_on_the_first_configured_preference(
    fast: Context, synth: Synth, tmp_path: Path
) -> None:
    preference = fast.config.accounts.layouts.preference
    assert preference[0] == "square"  # R8: the default layout
    fast.config.accounts.layouts.preference = ["full", "square"]
    post_id = post_on_video(fast, _outlined(synth(SOURCE), tmp_path), span=SHORT)
    assert _render(fast).done == 1
    _assert_video_at(_frame(_plate(fast, post_id), tmp_path), video_rect("full", 1280, 720))


# --- failure and idempotence ------------------------------------------------------------------


def _kill_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The final encode writes some bytes, then dies (the loudnorm pass is left alone)."""

    def dying(args: Any, **kwargs: Any) -> str:
        Path(str(args[-1])).write_bytes(b"partial")
        raise RuntimeError("tué pendant l'encodage")

    monkeypatch.setattr(encode, "run_ffmpeg", dying)


def test_interrupted_render_leaves_no_final_file(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    before = snapshot(fast)
    _kill_encoder(monkeypatch)
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    assert _files(fast) == []  # no plate, no final, no temporary file
    row = _post(fast, post_id)
    assert (row["status"], row["video_path"], row["render_version"]) == ("planned", None, None)
    assert snapshot(fast) == before


def test_interrupted_re_render_keeps_the_previous_files(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    before = snapshot(fast)
    _kill_encoder(monkeypatch)
    assert _render(fast, force=True).failed == 1
    assert snapshot(fast) == before  # files and rows exactly as the first render left them
    assert check_delivery(_final(fast, post_id)) == []


def test_r16_render_rerun_changes_nothing(fast: Context, synth: Synth) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    before = snapshot(fast)
    stamps = [p.stat().st_mtime_ns for p in (_plate(fast, post_id), _final(fast, post_id))]
    events = len(ctx_events(fast))
    report = _render(fast)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(fast) == before
    assert len(ctx_events(fast)) == events
    assert stamps == [p.stat().st_mtime_ns for p in (_plate(fast, post_id), _final(fast, post_id))]


def test_missing_clean_file_fails_the_post_with_a_french_message(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE))
    (fast.paths.episodes / f"e_{_episode_of(fast, post_id):06d}" / "clean.mp4").unlink()
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    failure = next(e for e in ctx_events(fast) if e.get("status") == "failed")
    assert "clean.mp4 introuvable" in failure["message"]
    assert _post(fast, post_id)["status"] == "planned"


def test_a_span_running_past_the_end_of_clean_mp4_fails_with_a_french_message(
    fast: Context, synth: Synth
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=(5000, 9000))  # the source lasts 6 s
    report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    failure = next(e for e in ctx_events(fast) if e.get("status") == "failed")
    assert "durée" in failure["message"] and "attendus" in failure["message"]
    assert _files(fast) == [] and _post(fast, post_id)["status"] == "planned"


def test_done_message_uses_the_french_decimal_comma(fast: Context, synth: Synth) -> None:
    post_on_video(fast, synth(SOURCE), span=SHORT)
    assert _render(fast).done == 1
    done = next(e for e in ctx_events(fast) if e.get("status") == "done")
    assert "2,0 s" in done["message"]


def _change_post_during_render(
    monkeypatch: pytest.MonkeyPatch, post_id: int, ctx: Context, sql: str
) -> None:
    """Another writer changes the post while its plate is being rendered."""
    real = render_module.render_plate

    def wrapped(*args: Any, **kwargs: Any) -> PlateReport | None:
        result = real(*args, **kwargs)
        with ctx.engine.begin() as conn:
            conn.execute(text(sql), {"i": post_id})
        return result

    monkeypatch.setattr(render_module, "render_plate", wrapped)


@pytest.mark.parametrize("status", ["discarded", "ready"])
def test_post_changed_during_the_render_is_skipped_and_its_files_stay(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    post_id = post_on_video(
        fast, synth(SOURCE), span=SHORT, status="rendered", render_version="plate-0"
    )
    for path in (_plate(fast, post_id), _final(fast, post_id)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old")
    _change_post_during_render(
        monkeypatch, post_id, fast, f"UPDATE posts SET status='{status}' WHERE id=:i"
    )
    report = _render(fast)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0), ctx_output(fast)
    skipped = next(e for e in ctx_events(fast) if e.get("status") == "skipped")
    assert "n'est plus à rendre" in skipped["message"]
    assert _files(fast) == sorted([_final(fast, post_id).name, _plate(fast, post_id).name])
    assert _plate(fast, post_id).read_bytes() == b"old" == _final(fast, post_id).read_bytes()
    assert (_post(fast, post_id)["status"], _post(fast, post_id)["render_version"]) == (
        status,
        "plate-0",
    )


def test_second_rename_failing_leaves_the_old_row_and_converges_on_the_next_run(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    final = _final(fast, post_id)
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == final:
            raise OSError("disque plein")
        real(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    row = _post(fast, post_id)  # rolled back: still to render
    assert (row["status"], row["video_path"], row["render_version"]) == ("planned", None, None)
    assert _files(fast) == [_plate(fast, post_id).name]  # new plate, no final, no temporary
    assert _render(fast).done == 1  # both files redone
    assert check_delivery(final) == [] and check_delivery(_plate(fast, post_id)) == []
    assert _render(fast).done == 0


def test_crash_between_the_rename_and_the_commit_converges_on_the_next_run(
    fast: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = post_on_video(fast, synth(SOURCE), span=SHORT)
    real = Staged.commit

    def commit_then_die(self: Staged) -> None:
        real(self)
        raise RuntimeError("tué entre le renommage et la validation")

    with monkeypatch.context() as patch:
        patch.setattr(Staged, "commit", commit_then_die)
        report = _render(fast)
    assert (report.done, report.failed) == (0, 1)
    row = _post(fast, post_id)  # the transaction rolled back: the post is still to render
    assert (row["status"], row["video_path"], row["render_version"]) == ("planned", None, None)
    assert [p.name for p in fast.paths.renders.iterdir() if p.name.startswith(".")] == []
    assert _render(fast).done == 1  # new files are simply redone
    assert check_delivery(_final(fast, post_id)) == []
    assert _post(fast, post_id)["status"] == "rendered"
    assert _render(fast).done == 0


@pytest.mark.slow
def test_r7_full_length_render_medium_preset(ctx: Context, synth: Synth, tmp_path: Path) -> None:
    assert ctx.config.settings.render.preset == "medium"
    quiet = _quiet_modulated(synth(SynthSpec(duration_ms=75_000)), tmp_path)
    post_id = post_on_video(ctx, quiet, span=(2000, 72_000))
    report = _render(ctx)
    assert (report.done, report.failed) == (1, 0), ctx_output(ctx)
    final = _final(ctx, post_id)
    assert check_delivery(final) == []
    assert abs(probe(final).duration_ms - 70_000) <= 150
    assert abs(_lufs(final) + 14.0) <= 1.0
    done = next(e for e in ctx_events(ctx) if e.get("status") == "done")
    assert "dynamique" not in done["message"]  # the second pass ran linear
