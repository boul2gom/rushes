import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import text

from fakes.models import FakeVad
from fixtures.generator import Synth, SynthSpec
from helpers import cleaned_episode, ctx_events, snapshot
from rushes.analyze import analyze as analyze_module
from rushes.analyze.analyze import AnalyzeStep, keyframe_time, shot_loudness, speech_ratio
from rushes.analyze.loudness import loudness_track
from rushes.analyze.motion import shot_motion
from rushes.analyze.shots import detect_shots
from rushes.analyze.speech import speech_spans
from rushes.context import Context, Filters
from rushes.core.phash import is_phash
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.media.audio import decode_pcm16k
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.frames import extract_frame, sample_gray
from rushes.media.probe import probe
from rushes.models.vad_silero import SileroVad
from rushes.runner import StepReport, run_step
from rushes.schemas import AudioFile, LoudPoint, ScenesFile, read_json

SMALL = (320, 180)
TINY = (64, 36)
FRAME_MS = 34
FILES = ("scenes.json", "audio.json", "keyframes/0.jpg")


def _analyze(ctx: Context, **filters: object) -> StepReport:
    return run_step(ctx, AnalyzeStep(), Filters(**filters))  # type: ignore[arg-type]


def _folder(ctx: Context, episode_id: int) -> Path:
    return ctx.paths.episode_dir(episode_key(episode_id))


def _status(ctx: Context, episode_id: int) -> str:
    with ctx.engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT status FROM episodes WHERE id=:i"), {"i": episode_id}
            ).scalar_one()
        )


def _rows(
    ctx: Context, episode_id: int
) -> list[tuple[int, int, float | None, float | None, float]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT start_ms, end_ms, loudness, motion, speech FROM scenes "
                "WHERE episode_id=:i ORDER BY id"
            ),
            {"i": episode_id},
        ).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def _analyzed(ctx: Context, video: Path) -> int:
    episode_id = cleaned_episode(ctx, video)
    report = _analyze(ctx)
    messages = [e.get("message") for e in ctx_events(ctx) if e.get("status") == "failed"]
    assert (report.done, report.failed) == (1, 0), messages
    return episode_id


# --- media helpers ----------------------------------------------------------------------------


def test_decode_pcm16k_is_mono_float_at_16k(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=3000, size=TINY, fps=10))
    pcm = decode_pcm16k(video)
    assert pcm.dtype == np.float32 and pcm.ndim == 1
    assert abs(len(pcm) - 3 * 16_000) <= 2000
    part = decode_pcm16k(video, span=(1000, 2000))
    assert abs(len(part) - 16_000) <= 400


def test_sample_gray_yields_timed_small_gray_images(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=2000, size=SMALL, fps=10))
    frames = list(sample_gray(video, fps=4.0, width=160))
    assert [t for t, _ in frames][:3] == [0, 250, 500]
    assert 7 <= len(frames) <= 9
    assert frames[0][1].dtype == np.uint8 and frames[0][1].shape == (90, 160)


def test_extract_frame_writes_a_jpeg_of_the_requested_width(synth: Synth, tmp_path: Path) -> None:
    from PIL import Image

    video = synth(SynthSpec(duration_ms=2000, size=(640, 360), fps=10))
    out = tmp_path / "k" / "0.jpg"
    extract_frame(video, 1000, out, width=320)
    with Image.open(out) as image:
        assert image.format == "JPEG" and image.size == (320, 180)
    assert [p.name for p in out.parent.iterdir()] == ["0.jpg"]


# --- detectors --------------------------------------------------------------------------------


def test_detect_shots_on_synthetic(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=10_000, size=SMALL, shots_ms=[3000, 7000]))
    shots = detect_shots(video)
    assert len(shots) == 3
    for (start, end), (want_start, want_end) in zip(
        shots, [(0, 3000), (3000, 7000), (7000, 10_000)], strict=True
    ):
        assert abs(start - want_start) <= FRAME_MS and abs(end - want_end) <= FRAME_MS


def test_detect_shots_without_a_cut_gives_one_shot(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=4000, size=SMALL))
    shots = detect_shots(video)
    assert len(shots) == 1 and shots[0][0] == 0 and abs(shots[0][1] - 4000) <= FRAME_MS


def test_loudness_track_peaks_on_beep(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=10_000, size=TINY, fps=10, beeps_ms=[(4000, 5000)]))
    integrated, points = loudness_track(video)
    assert integrated > -70.0
    assert points and all(isinstance(p, LoudPoint) for p in points)
    assert points[1].t_ms - points[0].t_ms == 100
    peak = max(points, key=lambda p: p.lufs)
    assert 4000 <= peak.t_ms <= 7000


def test_loudness_track_without_audio_is_silent_and_empty(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=2000, size=TINY, fps=10, audio=False))
    assert loudness_track(video) == (-70.0, [])


def test_motion_higher_on_moving_shot(synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=8000, size=SMALL, shots_ms=[4000], moving_target=[(4000, 0), (8000, 290)]
    )
    video = synth(spec)
    still, moving = shot_motion(video, [(0, 4000), (4000, 8000)])
    assert 0.0 <= still < moving <= 1.0


def test_motion_of_a_shot_with_fewer_than_two_samples_is_zero(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=2000, size=TINY, fps=10))
    assert shot_motion(video, [(0, 100)]) == [0.0]


def test_fake_vad_finds_speech_like_not_beeps(synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=10_000,
        size=TINY,
        fps=10,
        speech_like_ms=[(2000, 4000)],
        beeps_ms=[(6000, 7000)],
    )
    spans = speech_spans(FakeVad(), decode_pcm16k(synth(spec)))
    assert len(spans) == 1
    assert abs(spans[0][0] - 2000) <= 60 and abs(spans[0][1] - 4000) <= 60


def test_speech_spans_sorts_merges_and_drops_empty_spans() -> None:
    class Messy:
        def speech(self, pcm16k: object) -> list[tuple[int, int]]:
            return [(500, 900), (100, 400), (350, 450), (700, 700)]

    assert speech_spans(Messy(), np.zeros(1, dtype=np.float32)) == [(100, 450), (500, 900)]  # type: ignore[arg-type]


def test_shot_loudness_is_the_mean_of_its_points_or_none() -> None:
    points = [LoudPoint(t_ms=t, lufs=v) for t, v in [(0, -20.0), (100, -30.0), (200, -10.0)]]
    assert shot_loudness(points, (0, 200)) == pytest.approx(-25.0)
    assert shot_loudness(points, (200, 300)) == pytest.approx(-10.0)
    assert shot_loudness(points, (1000, 2000)) is None
    assert shot_loudness([], (0, 100)) is None


def test_speech_ratio_is_the_covered_share_of_the_shot() -> None:
    assert speech_ratio([(0, 500), (800, 900)], (0, 1000)) == pytest.approx(0.6)
    assert speech_ratio([], (0, 1000)) == 0.0
    assert speech_ratio([(0, 5000)], (1000, 2000)) == 1.0


# --- the step ---------------------------------------------------------------------------------


def test_scenes_json_matches_schema(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=10_000,
        size=SMALL,
        shots_ms=[3000, 7000],
        speech_like_ms=[(4000, 6000)],
    )
    episode_id = _analyzed(ctx, synth(spec))
    folder = _folder(ctx, episode_id)
    scenes = read_json(ScenesFile, folder / "scenes.json")
    audio = read_json(AudioFile, folder / "audio.json")
    assert scenes.episode_id == audio.episode_id == episode_id
    assert abs(scenes.duration_ms - 10_000) <= 100
    assert scenes.detector == {
        "adaptive_threshold": 3.0,
        "min_scene_len": 15.0,
        "window_width": 2.0,
        "min_content_val": 15.0,
    }
    assert [s.id for s in scenes.shots] == [0, 1, 2]
    assert scenes.shots[0].start_ms == 0 and scenes.shots[-1].end_ms == scenes.duration_ms
    for shot in scenes.shots:
        assert shot.start_ms < shot.keyframe_ms < shot.end_ms
        assert is_phash(shot.phash)
        assert (folder / "keyframes" / f"{shot.id}.jpg").is_file()
        assert 0.0 <= shot.motion <= 1.0 and 0.0 <= shot.speech_ratio <= 1.0
    rows = _rows(ctx, episode_id)
    assert len(rows) == len(scenes.shots)
    assert [(r[0], r[1]) for r in rows] == [(s.start_ms, s.end_ms) for s in scenes.shots]
    assert scenes.shots[1].speech_ratio > 0.4 and scenes.shots[0].speech_ratio == 0.0
    assert rows[1][4] == pytest.approx(scenes.shots[1].speech_ratio)
    assert audio.events is None and audio.integrated_lufs > -70.0
    assert audio.loudness and audio.speech and abs(audio.speech[0][0] - 4000) <= 100
    assert _status(ctx, episode_id) == "analyzed"


def test_shots_end_where_the_cleaned_video_ends(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=8000, size=SMALL, black_ms=[(0, 3000)], shots_ms=[5000])
    episode_id = _analyzed(ctx, synth(spec))  # clean cuts the 3 s of black: time 0 is clean.mp4's
    scenes = read_json(ScenesFile, _folder(ctx, episode_id) / "scenes.json")
    assert scenes.shots[0].start_ms == 0
    assert abs(scenes.duration_ms - 5000) <= 100
    assert scenes.shots[-1].end_ms == scenes.duration_ms
    assert len(scenes.shots) == 2
    assert abs(scenes.shots[1].start_ms - 2000) <= FRAME_MS  # source 5000 ms = clean.mp4 2000 ms


def test_video_without_audio_track_has_no_loudness_and_no_speech(
    ctx: Context, synth: Synth
) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10, audio=False)))
    folder = _folder(ctx, episode_id)
    audio = read_json(AudioFile, folder / "audio.json")
    # clean adds a silent track: loudness is the floor, nothing is speech
    assert audio.speech == [] and audio.integrated_lufs == -70.0
    assert all(p.lufs <= -70.0 for p in audio.loudness)
    scenes = read_json(ScenesFile, folder / "scenes.json")
    assert [s.speech_ratio for s in scenes.shots] == [0.0]


def _boom(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("recomputed")


def test_r16_analyze_rerun_changes_nothing(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    folder = _folder(ctx, episode_id)
    before = snapshot(ctx)
    report = _analyze(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 0, 0)
    assert snapshot(ctx) == before

    # a real crash: the rows and the status are gone, the files stay; the next run rebuilds the
    # rows from the files without computing anything again
    rows = _rows(ctx, episode_id)
    first = {n: (folder / n).read_bytes() for n in FILES}
    with ctx.engine.begin() as conn:
        conn.execute(text("DELETE FROM scenes"))
        conn.execute(text("UPDATE episodes SET status='cleaned'"))
    monkeypatch.setattr(analyze_module, "detect_shots", _boom)
    assert _analyze(ctx).done == 1
    assert _rows(ctx, episode_id) == rows and _status(ctx, episode_id) == "analyzed"
    assert {n: (folder / n).read_bytes() for n in FILES} == first


def test_analyze_force_on_an_analyzed_episode_says_why_nothing_happens(
    ctx: Context, synth: Synth
) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    before = snapshot(ctx)
    report = _analyze(ctx, force=True)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    messages = [e["message"] for e in ctx_events(ctx) if e.get("status") == "skipped"]
    assert any("déjà analysé" in m for m in messages)
    assert snapshot(ctx) == before and _status(ctx, episode_id) == "analyzed"


def test_analyze_ignores_episodes_that_are_not_cleaned(ctx: Context, synth: Synth) -> None:
    from helpers import drop_import
    from rushes.ingest.ingest import IngestStep

    drop_import(ctx, "tom_and_jerry", synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    run_step(ctx, IngestStep(), Filters())
    report = _analyze(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 0, 0)


def test_analyze_fails_readably_when_clean_files_are_missing(ctx: Context, synth: Synth) -> None:
    episode_id = cleaned_episode(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    (_folder(ctx, episode_id) / "clean.mp4").unlink()
    report = _analyze(ctx)
    assert (report.done, report.failed) == (0, 1)
    messages = [e["message"] for e in ctx_events(ctx) if e.get("status") == "failed"]
    assert any("clean" in m and episode_key(episode_id) in m for m in messages)
    assert _status(ctx, episode_id) == "cleaned" and _rows(ctx, episode_id) == []


def test_analyze_leaves_no_row_when_a_stage_fails(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rushes.analyze import analyze as module
    from rushes.errors import MediaError

    episode_id = cleaned_episode(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))

    def boom(*_a: object, **_k: object) -> None:
        raise MediaError("boom")

    monkeypatch.setattr(module, "shot_motion", boom)
    assert _analyze(ctx).failed == 1
    assert _status(ctx, episode_id) == "cleaned" and _rows(ctx, episode_id) == []


def test_analyze_runs_the_models_once_for_many_episodes(ctx: Context, synth: Synth) -> None:
    for index in range(2):
        cleaned_episode(ctx, synth(SynthSpec(duration_ms=2000 + index * 500, size=TINY, fps=10)))
    assert _analyze(ctx).done == 2  # r19: the hub loads the VAD once for the step
    assert ctx.models.loads()["vad"] == 1


# --- real model -------------------------------------------------------------------------------


@pytest.mark.slow
def test_silero_vad_runs_on_noise(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(16_000 * 3) * 0.1).astype(np.float32)
    spans = SileroVad().speech(noise)
    assert isinstance(spans, list)
    assert all(isinstance(s, tuple) and len(s) == 2 and 0 <= s[0] < s[1] for s in spans)


def _replace_clean(ctx: Context, episode_id: int, video: Path) -> None:
    """As if `clean --force` had produced `video` as clean.mp4 (status back to cleaned).

    The old mtime is kept, so that only the duration check can tell the analysis is out of date.
    """
    target = _folder(ctx, episode_id) / "clean.mp4"
    before = target.stat().st_mtime_ns
    shutil.copyfile(video, target)
    os.utime(target, ns=(before, before))
    with ctx.engine.begin() as conn:
        conn.execute(text("DELETE FROM scenes"))
        conn.execute(
            text("UPDATE episodes SET status='cleaned', duration_ms=:d"),
            {"d": probe(target).duration_ms},
        )


def test_r16_analyze_does_not_reuse_files_of_an_older_clean_mp4(ctx: Context, synth: Synth) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    folder = _folder(ctx, episode_id)
    assert abs(read_json(ScenesFile, folder / "scenes.json").duration_ms - 3000) <= 100
    _replace_clean(ctx, episode_id, synth(SynthSpec(duration_ms=2000, size=TINY, fps=10)))
    assert _analyze(ctx).done == 1
    scenes = read_json(ScenesFile, folder / "scenes.json")
    assert abs(scenes.duration_ms - 2000) <= 100 and scenes.shots[-1].end_ms == scenes.duration_ms
    assert [(r[0], r[1]) for r in _rows(ctx, episode_id)] == [
        (s.start_ms, s.end_ms) for s in scenes.shots
    ]


def test_r16_analyze_does_not_reuse_files_older_than_clean_mp4_of_the_same_length(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    clean = _folder(ctx, episode_id) / "clean.mp4"
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='cleaned'"))
    later = clean.stat().st_mtime + 60
    os.utime(clean, (later, later))  # re-encoded after the analysis
    calls: list[Path] = []
    real = analyze_module.detect_shots

    def spy(path: Path) -> list[tuple[int, int]]:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(analyze_module, "detect_shots", spy)
    assert _analyze(ctx).done == 1 and len(calls) == 1


def test_r16_analyze_force_recomputes_with_identical_files_and_survives_an_interruption(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    folder = _folder(ctx, episode_id)
    first = {n: (folder / n).read_bytes() for n in FILES}
    rows = _rows(ctx, episode_id)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='cleaned'"))
    monkeypatch.setattr(analyze_module, "loudness_track", _boom)
    assert _analyze(ctx, force=True).failed == 1
    assert not (folder / "audio.json").exists()  # the marker went first
    monkeypatch.undo()
    assert _analyze(ctx).done == 1  # an interrupted recompute is not taken for finished
    assert {n: (folder / n).read_bytes() for n in FILES} == first
    assert _rows(ctx, episode_id) == rows and _status(ctx, episode_id) == "analyzed"


def test_analyze_store_refuses_an_episode_that_is_no_longer_cleaned(
    ctx: Context, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=4000, size=SMALL, shots_ms=[2000])
    episode_id = _analyzed(ctx, synth(spec))
    rows = _rows(ctx, episode_id)
    shots = read_json(ScenesFile, _folder(ctx, episode_id) / "scenes.json").shots
    assert len(shots) == 2 and len(rows) == 2
    with pytest.raises(RushesError):  # the status is 'analyzed': the UPDATE touches no row
        AnalyzeStep._store(ctx, episode_id, shots[:1])
    assert _rows(ctx, episode_id) == rows  # the DELETE and INSERT were rolled back


def _long_audio(tmp_path: Path) -> Path:
    """2 s of picture, 4 s of sound: the container is longer than the video stream."""
    out = tmp_path / "long_audio.mp4"
    args: list[str | Path] = ["-f", "lavfi", "-i", "testsrc2=s=64x36:r=10:d=2"]
    args += ["-f", "lavfi", "-i", "sine=f=440:d=4", "-c:v", "libx264", "-preset", "ultrafast"]
    args += ["-c:a", "aac", "-f", "mp4", out]
    run_ffmpeg(args)
    return out


def test_extract_frame_past_the_last_frame_is_a_readable_error(tmp_path: Path) -> None:
    video = _long_audio(tmp_path)
    with pytest.raises(MediaError, match=r"long_audio\.mp4"):
        extract_frame(video, 3500, tmp_path / "k" / "x.jpg")
    assert not (tmp_path / "k" / "x.jpg").exists()


def test_analyze_keeps_the_key_image_on_the_picture_when_sound_outlasts_it(
    ctx: Context, tmp_path: Path
) -> None:
    episode_id = _analyzed(ctx, _long_audio(tmp_path))
    folder = _folder(ctx, episode_id)
    scenes = read_json(ScenesFile, folder / "scenes.json")
    picture_ms = probe(folder / "clean.mp4").video_duration_ms
    assert scenes.shots[-1].end_ms == scenes.duration_ms
    assert all(s.keyframe_ms < picture_ms for s in scenes.shots)
    assert all((folder / "keyframes" / f"{s.id}.jpg").stat().st_size > 0 for s in scenes.shots)


def test_temporary_files_go_under_the_given_folder_and_are_removed(
    synth: Synth, tmp_path: Path
) -> None:
    video = synth(SynthSpec(duration_ms=2000, size=TINY, fps=10))
    scratch = tmp_path / "scratch"
    decode_pcm16k(video, tmp_dir=scratch)
    list(sample_gray(video, fps=4.0, width=32, tmp_dir=scratch))
    assert scratch.is_dir() and list(scratch.iterdir()) == []


def test_analyze_step_puts_its_temporary_files_under_data_tmp(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tempfile.TemporaryDirectory
    seen: list[tuple[object, object]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append((kwargs.get("prefix"), kwargs.get("dir")))
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", spy)
    _analyzed(ctx, synth(SynthSpec(duration_ms=3000, size=TINY, fps=10)))
    mine = [(prefix, folder) for prefix, folder in seen if prefix in ("audio-", "frames-")]
    assert {prefix for prefix, _ in mine} == {"audio-", "frames-"}
    assert all(folder == ctx.paths.tmp for _, folder in mine)


def test_shot_motion_ignores_samples_between_shots(synth: Synth) -> None:
    spec = SynthSpec(duration_ms=4000, size=SMALL, fps=10, moving_target=[(0, 0), (4000, 290)])
    video = synth(spec)
    first = shot_motion(video, [(0, 1500)])[0]
    second = shot_motion(video, [(2500, 4000)])[0]
    assert first > 0.0 and second > 0.0
    # the samples of the gap (1500..2500) belong to no shot and no difference crosses it
    assert shot_motion(video, [(0, 1500), (2500, 4000)]) == [first, second]
    # an empty shot counts for 0.0 and disturbs nothing
    assert shot_motion(video, [(0, 1500), (1500, 1500), (2500, 4000)]) == [first, 0.0, second]


def test_shot_motion_refuses_unsorted_or_overlapping_shots(synth: Synth) -> None:
    video = synth(SynthSpec(duration_ms=2000, size=TINY, fps=10))
    with pytest.raises(ValueError, match="triés et disjoints"):
        shot_motion(video, [(1000, 2000), (0, 1000)])
    with pytest.raises(ValueError, match="triés et disjoints"):
        shot_motion(video, [(0, 1500), (1000, 2000)])
    assert len(shot_motion(video, [(0, 1000), (1000, 2000)])) == 2  # adjacent is fine


def test_keyframe_time_is_the_middle_held_back_to_the_last_frame() -> None:
    assert keyframe_time((0, 1000), 5000) == 500
    assert keyframe_time((2000, 4000), 2500) == 2500  # sound outlasts the picture
    assert keyframe_time((3000, 4000), 2500) == 3000  # never before the start of the shot
