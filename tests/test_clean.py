import dataclasses
import json
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from fixtures.generator import Synth, SynthSpec
from helpers import CREATOR, SERIES, audio_peak, ctx_events, drop_import, snapshot, with_config
from rushes.clean.borders import border_spans, kept_range
from rushes.clean.clean import CleanStep, cartoon_removed
from rushes.context import Context, Filters
from rushes.core.intervals import Span
from rushes.errors import MediaError
from rushes.ids import episode_key
from rushes.ingest.ingest import IngestStep
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import ProbeInfo, probe
from rushes.runner import StepReport, run_step
from rushes.schemas import CleanFile, read_json
from rushes.ui import Progress

SMALL = (320, 180)
FRAME_MS = 34  # one frame at 30 fps
EditYaml = Callable[[Path, Callable[[Any], Any]], None]


def _import(ctx: Context, video: Path, folder: str = SERIES) -> int:
    drop_import(ctx, folder, video)
    assert run_step(ctx, IngestStep(), Filters()).done == 1
    with ctx.engine.connect() as conn:
        return int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())


def _clean(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, CleanStep(preset="ultrafast"), Filters(**filters))


def _status(ctx: Context, episode_id: int) -> str:
    with ctx.engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT status FROM episodes WHERE id=:i"), {"i": episode_id}
            ).scalar_one()
        )


def _folder(ctx: Context, episode_id: int) -> Path:
    return ctx.paths.episode_dir(episode_key(episode_id))


def _clean_file(ctx: Context, episode_id: int) -> CleanFile:
    return read_json(CleanFile, _folder(ctx, episode_id) / "clean.json")


def _ffprobe_streams(path: Path) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(done.stdout)["streams"])


def _run(ctx: Context, video: Path, folder: str = SERIES) -> tuple[int, CleanFile]:
    episode_id = _import(ctx, video, folder)
    report = _clean(ctx)
    messages = [e.get("message") for e in ctx_events(ctx) if e.get("status") == "failed"]
    assert (report.done, report.failed) == (1, 0), messages
    return episode_id, _clean_file(ctx, episode_id)


# --- pure helpers -----------------------------------------------------------------------------


def test_border_spans_keeps_only_long_spans_touching_a_border() -> None:
    spans = [(0, 3000), (30_000, 34_000), (57_000, 60_000), (10_000, 11_000)]
    assert border_spans(spans, 60_000) == [(0, 3000), (57_000, 60_000)]


def test_border_spans_is_strict_about_2_seconds() -> None:
    assert border_spans([(0, 2000)], 60_000) == []
    assert border_spans([(0, 2001)], 60_000) == [(0, 2001)]
    assert border_spans([(0, 1500)], 60_000) == []


def test_border_spans_measures_the_raw_span_not_the_snapped_one() -> None:
    # 1.95 s of black that starts 100 ms after the start is not "more than 2 s"
    assert border_spans([(100, 2050)], 60_000) == []
    assert border_spans([(57_950, 59_900)], 60_000) == []
    assert border_spans([(100, 2150)], 60_000) == [(0, 2150)]


def test_border_spans_snaps_near_borders_within_eps() -> None:
    assert border_spans([(60, 3000)], 60_000) == [(0, 3000)]
    assert border_spans([(57_000, 59_950)], 60_000) == [(57_000, 60_000)]
    assert border_spans([(150, 3000)], 60_000) == []  # farther than eps_ms from the start
    assert border_spans([(150, 3000)], 60_000, eps_ms=200) == [(0, 3000)]


def test_kept_range_is_what_lies_between_the_two_borders() -> None:
    assert kept_range(60_000, []) == (0, 60_000)
    assert kept_range(60_000, [(0, 3000)]) == (3000, 60_000)
    assert kept_range(60_000, [(0, 3000), (0, 5000), (57_000, 60_000)]) == (5000, 57_000)
    assert kept_range(60_000, [(0, 3000), (3000, 4500)]) == (4500, 60_000)  # adjacent zones chain


def test_kept_range_ignores_zones_that_touch_no_border() -> None:
    assert kept_range(60_000, [(30_000, 34_000)]) == (0, 60_000)


def test_kept_range_with_nothing_left_raises() -> None:
    with pytest.raises(ValueError):
        kept_range(10_000, [(0, 10_000)])


# --- borders ----------------------------------------------------------------------------------


def test_border_black_over_2s_removed(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, black_ms=[(0, 3000)])
    episode_id, clean = _run(ctx, synth(spec))
    assert abs(clean.kept[0] - 3000) <= FRAME_MS
    assert abs(clean.kept[1] - clean.source_duration_ms) <= FRAME_MS
    assert [kind for *_, kind in clean.removed] == ["black"]
    assert clean.removed[0][0] == 0
    out = probe(_folder(ctx, episode_id) / "clean.mp4")
    assert abs(out.duration_ms - (clean.kept[1] - clean.kept[0])) <= 80
    assert _status(ctx, episode_id) == "cleaned"


def test_border_black_under_2s_kept(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, black_ms=[(0, 1500)])
    _, clean = _run(ctx, synth(spec))
    assert clean.kept[0] == 0
    assert clean.removed == []


def test_border_silence_over_2s_at_the_end_removed(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=12_000, size=SMALL, silence_ms=[(8000, 12_000)])
    _, clean = _run(ctx, synth(spec))
    assert clean.kept[0] == 0
    assert abs(clean.kept[1] - 8000) <= 60
    assert [kind for *_, kind in clean.removed] == ["silence"]


def test_r45_cartoon_interior_silence_kept(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=30_000, size=(160, 90), fps=10, silence_ms=[(12_000, 16_000)])
    episode_id, clean = _run(ctx, synth(spec))
    assert clean.removed == []
    assert clean.kept == (0, clean.source_duration_ms)
    assert abs(probe(_folder(ctx, episode_id) / "clean.mp4").duration_ms - 30_000) <= 100
    with ctx.engine.connect() as conn:
        stored = conn.execute(text("SELECT duration_ms FROM episodes")).scalar_one()
    assert abs(stored - 30_000) <= 100  # duration_ms follows clean.mp4 once cleaned


def test_r45_cartoon_interior_black_kept(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=20_000, size=SMALL, black_ms=[(8000, 12_000)])
    _, clean = _run(ctx, synth(spec))
    assert clean.removed == []


# --- title card -------------------------------------------------------------------------------


def test_title_card_removed_via_freeze(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=20_000, size=SMALL, title_card_ms=(0, 4000))
    _, clean = _run(ctx, synth(spec))
    assert abs(clean.kept[0] - 4000) <= 100
    assert [kind for *_, kind in clean.removed] == ["title_card"]


def test_title_card_after_black_chains_the_removal(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=20_000, size=SMALL, black_ms=[(0, 2500)], title_card_ms=(2500, 5000)
    )
    _, clean = _run(ctx, synth(spec))
    assert abs(clean.kept[0] - 5000) <= 100
    kinds = [kind for *_, kind in clean.removed]
    assert kinds[0] == "black" and "title_card" in kinds


def test_title_card_in_the_middle_is_kept(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=20_000, size=SMALL, title_card_ms=(8000, 11_000))
    _, clean = _run(ctx, synth(spec))
    assert clean.removed == []


def test_title_card_too_short_is_kept(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, title_card_ms=(0, 1000))
    _, clean = _run(ctx, synth(spec))
    assert clean.removed == []


# --- opening and closing credits from reference frames ----------------------------------------

SHOTS = SynthSpec(duration_ms=20_000, size=SMALL, shots_ms=[5000, 12_000, 15_000])


def _reference(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, video: Path, at_s: int
) -> Context:
    """A reference image cut from the video itself, registered for the series."""
    ref = config_dir / "refs" / f"frame_{at_s}.png"
    ref.parent.mkdir(exist_ok=True)
    run_ffmpeg(["-ss", str(at_s), "-i", str(video), "-frames:v", "1", str(ref)])

    def add(data: Any) -> None:
        data["intro_outro"] = {"reference_frames": [f"refs/{ref.name}"]}

    edit_yaml(config_dir / "series" / f"{SERIES}.yaml", add)
    return with_config(ctx, config_dir)


def test_intro_removed_via_reference_frame(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    video = synth(SHOTS)
    context = _reference(ctx, config_dir, edit_yaml, video, at_s=2)  # inside the first shot
    _, clean = _run(context, video)
    assert abs(clean.kept[0] - 5000) <= 500  # two frames per second
    assert abs(clean.kept[1] - clean.source_duration_ms) <= FRAME_MS
    assert [kind for *_, kind in clean.removed] == ["intro"]


def test_outro_removed_via_reference_frame(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    video = synth(SHOTS)
    context = _reference(ctx, config_dir, edit_yaml, video, at_s=17)  # inside the last shot
    _, clean = _run(context, video)
    assert clean.kept[0] == 0
    assert abs(clean.kept[1] - 15_000) <= 500
    assert [kind for *_, kind in clean.removed] == ["outro"]


def test_reference_frame_matching_the_middle_removes_nothing(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    video = synth(SHOTS)
    context = _reference(ctx, config_dir, edit_yaml, video, at_s=13)  # a shot touching no border
    _, clean = _run(context, video)
    assert clean.removed == []


# --- normalization ----------------------------------------------------------------------------


def test_clean_normalizes_vfr_sar_and_missing_audio(ctx: Context, synth: Synth) -> None:
    source = SynthSpec(duration_ms=3000, size=(1440, 1080), sar=(4, 3), vfr=True, audio=False)
    episode_id, _ = _run(ctx, synth(source))
    out = probe(_folder(ctx, episode_id) / "clean.mp4")
    assert (out.width, out.height, out.display_width) == (1920, 1080, 1920)
    assert out.fps == Fraction(30) and not out.vfr
    assert out.has_audio and out.acodec == "aac"
    facts = _ffprobe_streams(_folder(ctx, episode_id) / "clean.mp4")
    audio = next(s for s in facts if s["codec_type"] == "audio")
    video = next(s for s in facts if s["codec_type"] == "video")
    assert (int(audio["sample_rate"]), audio["channels"]) == (48000, 2)
    assert video.get("sample_aspect_ratio", "1:1") == "1:1"


def test_clean_keeps_the_audio_track_when_there_is_one(ctx: Context, synth: Synth) -> None:
    episode_id, _ = _run(
        ctx, synth(SynthSpec(duration_ms=3000, size=SMALL, beeps_ms=[(1000, 2000)]))
    )
    peak = audio_peak(_folder(ctx, episode_id) / "clean.mp4", 1.2, 0.6)
    assert peak > 0.5  # the beep (0.9) is there, not a muted track


# --- idempotence and failures -----------------------------------------------------------------


def test_r16_clean_rerun_changes_nothing(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=8000, size=SMALL, black_ms=[(0, 3000)])
    episode_id, _ = _run(ctx, synth(spec))
    before = snapshot(ctx)
    report = _clean(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(ctx) == before
    assert _status(ctx, episode_id) == "cleaned"


def test_clean_force_redoes_cleaned_episodes(ctx: Context, synth: Synth) -> None:
    episode_id, first = _run(ctx, synth(SynthSpec(duration_ms=5000, size=SMALL)))
    report = _clean(ctx, force=True)
    assert report.done == 1
    assert _clean_file(ctx, episode_id) == first
    assert _status(ctx, episode_id) == "cleaned"


def test_clean_resumes_without_encoding_again(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id, first = _run(ctx, synth(SynthSpec(duration_ms=5000, size=SMALL)))
    with ctx.engine.begin() as conn:  # the process died after the files, before the database
        conn.execute(text("UPDATE episodes SET status='imported'"))

    def boom(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("ffmpeg must not run again")

    monkeypatch.setattr("rushes.clean.clean.run_ffmpeg", boom)
    monkeypatch.setattr("rushes.clean.clean.detect_black", boom)
    assert _clean(ctx).done == 1
    assert _status(ctx, episode_id) == "cleaned"
    assert _clean_file(ctx, episode_id) == first


def test_clean_entirely_black_episode_fails_and_stays_imported(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=5000, size=SMALL, black_ms=[(0, 5000)])
    episode_id = _import(ctx, synth(spec))
    report = _clean(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert _status(ctx, episode_id) == "imported"
    assert not (_folder(ctx, episode_id) / "clean.mp4").exists()


def test_clean_leaves_no_partial_file_when_ffmpeg_fails(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _import(ctx, synth(SynthSpec(duration_ms=3000, size=SMALL)))
    calls: list[str] = []

    def dying_ffmpeg(args: Any, **kwargs: Any) -> str:
        calls.append("encode")
        Path(str(args[-1])).write_bytes(b"half a file")  # the temporary output, then the crash
        raise MediaError("ffmpeg a échoué (code 1)")

    monkeypatch.setattr("rushes.clean.clean.run_ffmpeg", dying_ffmpeg)
    assert _clean(ctx).failed == 1
    assert calls == ["encode"]  # the failure comes from the encoding, not from the probe
    assert list(_folder(ctx, episode_id).glob("*")) == []  # no clean.mp4, no .clean.mp4.tmp-*
    assert list(_folder(ctx, episode_id).glob(".*")) == []
    assert _status(ctx, episode_id) == "imported"


def test_clean_creator_episode_is_only_normalized(ctx: Context, synth: Synth) -> None:
    # the creators' own cleaning (SponsorBlock, autocut) is task 24: nothing is cut here
    spec = SynthSpec(duration_ms=8000, size=SMALL, black_ms=[(0, 3000)])
    _, clean = _run(ctx, synth(spec), folder=CREATOR)
    assert clean.removed == [] and clean.kept == (0, clean.source_duration_ms)


def test_clean_filters_select_the_episode(ctx: Context, synth: Synth) -> None:
    first = _import(ctx, synth(SynthSpec(duration_ms=3000, size=SMALL)))
    second = _import(ctx, synth(SynthSpec(duration_ms=3500, size=SMALL)))
    assert _clean(ctx, episode=second).done == 1
    assert (_status(ctx, first), _status(ctx, second)) == ("imported", "cleaned")
    assert _clean(ctx, series=SERIES).done == 1
    assert _status(ctx, first) == "cleaned"


# --- review fixes: M2, M3, M4, M15 ----------------------------------------------------------


def test_closing_black_running_to_the_end_is_cut(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, black_ms=[(6000, 10_000)])
    _, clean = _run(ctx, synth(spec))
    assert abs(clean.kept[1] - 6000) <= FRAME_MS
    assert [kind for *_, kind in clean.removed] == ["black"]
    assert clean.removed[0][1] == clean.source_duration_ms  # snapped to the end of the file


def test_black_after_a_title_card_is_chained(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=20_000, size=SMALL, title_card_ms=(0, 4000), black_ms=[(4000, 7000)]
    )
    _, clean = _run(ctx, synth(spec))
    assert abs(clean.kept[0] - 7000) <= 100
    assert [kind for *_, kind in clean.removed] == ["title_card", "black"]


def test_intro_after_a_title_card_is_chained(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(
        duration_ms=20_000, size=SMALL, shots_ms=[8000, 12_000, 15_000], title_card_ms=(0, 3000)
    )
    video = synth(spec)
    context = _reference(ctx, config_dir, edit_yaml, video, at_s=5)  # first shot, after the card
    _, clean = _run(context, video)
    assert abs(clean.kept[0] - 8000) <= 500
    assert [kind for *_, kind in clean.removed] == ["title_card", "intro"]


def test_clean_force_on_an_analyzed_episode_says_why_nothing_happens(
    ctx: Context, synth: Synth
) -> None:
    episode_id, _ = _run(ctx, synth(SynthSpec(duration_ms=4000, size=SMALL)))
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='analyzed'"))
    before = snapshot(ctx)
    report = _clean(ctx, force=True)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    messages = [e["message"] for e in ctx_events(ctx) if e.get("status") == "skipped"]
    assert any("déjà analysé" in m for m in messages)
    assert snapshot(ctx) == before and _status(ctx, episode_id) == "analyzed"
    assert _clean(ctx).skipped == 0  # without --force the episode is not even selected


def test_clean_reports_every_percentage_change_in_percent(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    class Recorder:
        def emit(self, event: Any) -> None:
            seen.append(event)

        def close(self) -> None:
            pass

    real = run_ffmpeg

    def spy(args: Any, *, on_progress: Any = None, **kwargs: Any) -> str:
        assert on_progress is not None
        for percent in (3, 8, 12, 100):  # jumps, as on a short episode
            on_progress(percent)
        return real(args, **kwargs)

    monkeypatch.setattr("rushes.clean.clean.run_ffmpeg", spy)
    context = dataclasses.replace(ctx, ui=Recorder())
    _import(ctx, synth(SynthSpec(duration_ms=3000, size=SMALL)))
    assert _clean(context).done == 1
    ticks = [e for e in seen if isinstance(e, Progress)]
    assert [t.done for t in ticks] == [3, 8, 12, 100]
    assert all(t.unit == "%" and t.total == 100 for t in ticks)


# --- chaining with stubbed detectors: edges, the video end, and what stays interior ----------


def _info(duration_ms: int, video_duration_ms: int | None = None) -> ProbeInfo:
    return ProbeInfo(
        duration_ms=duration_ms,
        width=320,
        height=180,
        display_width=320,
        fps=Fraction(30),
        vfr=False,
        has_audio=True,
        vcodec="h264",
        acodec="aac",
        video_duration_ms=duration_ms if video_duration_ms is None else video_duration_ms,
    )


def _removed(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    info: ProbeInfo,
    *,
    blacks: list[Span] = (),  # type: ignore[assignment]
    silences: list[Span] = (),  # type: ignore[assignment]
    freezes: list[Span] = (),  # type: ignore[assignment]
) -> list[tuple[int, int, str]]:
    monkeypatch.setattr("rushes.clean.clean.detect_black", lambda *a, **k: list(blacks))
    monkeypatch.setattr("rushes.clean.clean.detect_silence", lambda *a, **k: list(silences))
    monkeypatch.setattr("rushes.clean.clean.detect_freeze", lambda *a, **k: list(freezes))
    return list(cartoon_removed(ctx, Path("unused.mp4"), SERIES, info))


def test_closing_black_is_cut_when_the_audio_outlasts_the_video(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # video 8.0 s, container 8.3 s (audio): blackdetect ends at the last video frame, 7967 ms,
    # 333 ms before the container end: only the video end tells it touches the edge
    info = _info(8300, 8000)
    removed = _removed(ctx, monkeypatch, info, blacks=[(4000, 7967)])
    assert removed == [(4000, 8300, "black")]
    assert kept_range(8300, [(s, e) for s, e, _ in removed]) == (0, 4000)


def test_black_ending_well_before_the_video_end_is_kept(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _removed(ctx, monkeypatch, _info(8300, 8000), blacks=[(4000, 7500)]) == []


def test_c38_silence_after_a_title_card_but_beyond_eps_stays_interior(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = {"freezes": [(0, 4000)]}
    near = _removed(ctx, monkeypatch, _info(20_000), silences=[(4050, 8000)], **kwargs)
    assert [(s, e, k) for s, e, k in near] == [(0, 4000, "title_card"), (4000, 8000, "silence")]
    far = _removed(ctx, monkeypatch, _info(20_000), silences=[(4500, 8000)], **kwargs)
    assert far == [(0, 4000, "title_card")]


def test_c38_black_after_a_title_card_but_far_from_any_edge_is_kept(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = _removed(
        ctx, monkeypatch, _info(60_000), blacks=[(30_000, 34_000)], freezes=[(0, 4000)]
    )
    assert removed == [(0, 4000, "title_card")]
    assert kept_range(60_000, [(s, e) for s, e, _ in removed]) == (4000, 60_000)


def test_c38_freeze_far_after_a_title_card_is_kept(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = _removed(ctx, monkeypatch, _info(60_000), freezes=[(0, 4000), (15_000, 45_000)])
    assert removed == [(0, 4000, "title_card")]


def test_c38_a_second_black_far_from_the_edge_is_kept(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = _removed(ctx, monkeypatch, _info(60_000), blacks=[(0, 3000), (10_000, 13_000)])
    assert removed == [(0, 3000, "black")]


def test_c38_blacks_chained_within_eps_all_go(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    blacks = [(0, 3000), (3050, 6000), (6120, 9000)]  # gaps of 50 and 120 ms (eps is 134 ms)
    removed = _removed(ctx, monkeypatch, _info(60_000), blacks=blacks)
    assert kept_range(60_000, [(s, e) for s, e, _ in removed]) == (9000, 60_000)


def test_clean_force_removes_the_analysis_of_the_old_clean_mp4(ctx: Context, synth: Synth) -> None:
    episode_id, _ = _run(ctx, synth(SynthSpec(duration_ms=4000, size=SMALL)))
    folder = _folder(ctx, episode_id)
    (folder / "keyframes").mkdir()
    for name in ("scenes.json", "audio.json", "keyframes/0.jpg"):
        (folder / name).write_bytes(b"old")
    assert _clean(ctx, force=True).done == 1
    assert not (folder / "scenes.json").exists() and not (folder / "audio.json").exists()
    assert not (folder / "keyframes").exists()
    assert (folder / "clean.mp4").is_file()
