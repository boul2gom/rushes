import hashlib
import math
from fractions import Fraction
from pathlib import Path

import pytest

from fixtures.generator import Synth, SynthSpec
from helpers import audio_peak
from rushes.errors import MediaError
from rushes.media.detect import (
    detect_black,
    detect_freeze,
    detect_silence,
    parse_blackdetect,
    parse_freezedetect,
    parse_silencedetect,
)
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import parse_probe, probe, sha256_file

LOGS = Path(__file__).parent / "fixtures" / "ffmpeg_logs"
SMALL = (320, 180)
# black 0-3 s, silence 8-11 s, title card (frozen picture) 14-16.5 s: the logs in
# fixtures/ffmpeg_logs were captured from a run of ffmpeg on this video.
SIGNALS = SynthSpec(
    duration_ms=20_000,
    size=SMALL,
    black_ms=[(0, 3000)],
    silence_ms=[(8000, 11_000)],
    title_card_ms=(14_000, 16_500),
)
EXOTIC = SynthSpec(duration_ms=3000, size=(1440, 1080), sar=(4, 3), vfr=True, audio=False)


def _near(spans: list[tuple[int, int]], expected: list[tuple[int, int]], tol: int = 70) -> None:
    assert len(spans) == len(expected), spans
    for (start, end), (want_start, want_end) in zip(spans, expected, strict=True):
        assert abs(start - want_start) <= tol and abs(end - want_end) <= tol, (spans, expected)


# --- probe ----------------------------------------------------------------------------------


def test_probe_reads_synthetic(synth: Synth) -> None:
    info = probe(synth(SynthSpec(duration_ms=8000)))
    assert abs(info.duration_ms - 8000) <= 40
    assert (info.width, info.height, info.display_width) == (1280, 720, 1280)
    assert info.fps == Fraction(30)
    assert not info.vfr
    assert info.has_audio
    assert (info.vcodec, info.acodec) == ("h264", "aac")


def test_probe_reports_the_video_stream_duration(synth: Synth) -> None:
    info = probe(synth(SynthSpec(duration_ms=8000, size=SMALL)))
    assert abs(info.video_duration_ms - 8000) <= 40
    assert info.video_duration_ms <= info.duration_ms


def _stream_json(**video: object) -> dict[str, object]:
    stream = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
    stream |= {"avg_frame_rate": "30/1", "r_frame_rate": "30/1"} | video
    audio = {"codec_type": "audio", "codec_name": "aac", "duration": "10.000"}
    return {"format": {"duration": "10.050"}, "streams": [stream, audio]}


def test_parse_probe_swaps_dimensions_for_a_rotated_phone_clip() -> None:
    plain = parse_probe(_stream_json(), "a.mp4")
    assert (plain.width, plain.height, plain.display_width) == (1920, 1080, 1920)
    side = {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}
    turned = parse_probe(_stream_json(**side), "a.mp4")
    assert (turned.width, turned.height, turned.display_width) == (1080, 1920, 1080)
    legacy = parse_probe(_stream_json(tags={"rotate": "270"}), "a.mp4")
    assert (legacy.width, legacy.height) == (1080, 1920)
    upside_down = parse_probe(_stream_json(tags={"rotate": "180"}), "a.mp4")
    assert (upside_down.width, upside_down.height) == (1920, 1080)


def test_parse_probe_video_duration_falls_back_to_the_container() -> None:
    data = _stream_json(duration="9.980")
    assert parse_probe(data, "a.mp4").video_duration_ms == 9980
    assert parse_probe(data, "a.mp4").duration_ms == 10_050
    assert parse_probe(_stream_json(), "a.mp4").video_duration_ms == 10_050


def test_probe_reads_pixel_aspect_vfr_and_missing_audio(synth: Synth) -> None:
    info = probe(synth(EXOTIC))
    assert (info.width, info.height) == (1440, 1080)
    assert info.display_width == 1920  # 1440 x 4/3
    assert info.vfr
    assert not info.has_audio and info.acodec is None


def test_probe_unreadable_file_raises_media_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(MediaError):
        probe(broken)
    with pytest.raises(MediaError):
        probe(tmp_path / "absent.mp4")


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc" * 1_000_000)  # larger than one read block
    assert sha256_file(path) == hashlib.sha256(b"abc" * 1_000_000).hexdigest()


# --- run_ffmpeg -------------------------------------------------------------------------------


def test_run_ffmpeg_reports_progress_up_to_100(tmp_path: Path) -> None:
    seen: list[int] = []
    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=s=64x64:r=30:d=2", "-f", "null", "-"],
        duration_ms=2000,
        on_progress=seen.append,
    )
    assert seen and seen == sorted(seen) and seen[-1] == 100


def test_run_ffmpeg_error_carries_the_last_20_stderr_lines(tmp_path: Path) -> None:
    with pytest.raises(MediaError) as raised:
        run_ffmpeg(["-i", str(tmp_path / "absent.mp4"), "-f", "null", "-"])
    message = str(raised.value)
    assert "absent.mp4" in message
    assert len(message.splitlines()) <= 22  # headline + at most 20 lines


def test_run_ffmpeg_returns_stderr(tmp_path: Path) -> None:
    log = run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=64x64:d=1", "-f", "null", "-"])
    assert "frame=" in log or "Output #0" in log


# --- detectors: parsers on captured logs ------------------------------------------------------


def test_parse_blackdetect_from_captured_log() -> None:
    assert parse_blackdetect((LOGS / "blackdetect.txt").read_text()) == [(0, 3000)]


def test_parse_silencedetect_from_captured_log() -> None:
    _near(parse_silencedetect((LOGS / "silencedetect.txt").read_text()), [(8000, 11_000)])


def test_parse_freezedetect_from_captured_log() -> None:
    spans = parse_freezedetect((LOGS / "freezedetect.txt").read_text())
    _near(spans, [(0, 3000), (14_000, 16_500)], 100)  # the black frames are frozen too


def test_parsers_ignore_noise_and_empty_logs() -> None:
    noise = "Input #0, mov,mp4\n  Duration: 00:00:20.00\nframe=  10 fps=0.0\n"
    assert parse_blackdetect(noise) == []
    assert parse_silencedetect(noise) == []
    assert parse_freezedetect(noise) == []


def test_parse_silencedetect_closes_a_dangling_start_with_end_ms() -> None:
    log = "[silencedetect @ 0x1] silence_start: 18.5\n"
    assert parse_silencedetect(log) == []
    assert parse_silencedetect(log, end_ms=20_000) == [(18_500, 20_000)]


def test_parse_freezedetect_closes_a_dangling_start_with_end_ms() -> None:
    log = "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 4.2\n"
    assert parse_freezedetect(log) == []
    assert parse_freezedetect(log, end_ms=10_000) == [(4200, 10_000)]


# --- detectors on a synthetic video -----------------------------------------------------------


def test_detectors_use_the_given_duration_instead_of_probing_again(
    synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = synth(SIGNALS)

    def no_probe(*args: object, **kwargs: object) -> None:
        raise AssertionError("probe must not run again")

    monkeypatch.setattr("rushes.media.detect.probe", no_probe)
    assert detect_silence(path, min_ms=4000, duration_ms=20_000) == []
    assert detect_freeze(path, min_ms=4000, duration_ms=20_000) == []


def test_detect_black_finds_the_leading_black(synth: Synth) -> None:
    path = synth(SIGNALS)
    _near(detect_black(path, min_ms=1000), [(0, 3000)])
    assert detect_black(path, min_ms=4000) == []


def test_detect_silence_finds_the_gap(synth: Synth) -> None:
    path = synth(SIGNALS)
    _near(detect_silence(path, min_ms=1000), [(8000, 11_000)])
    assert detect_silence(path, min_ms=4000) == []


def test_detect_freeze_finds_the_title_card(synth: Synth) -> None:
    path = synth(SIGNALS)
    spans = detect_freeze(path, min_ms=1500)
    # the black frames are frozen too
    assert any(abs(s - 14_000) <= 100 and abs(e - 16_500) <= 100 for s, e in spans), spans
    assert all(e - s >= 1500 for s, e in spans)


# --- generator levels (contract of docs/plans/00-master.md section 6) -------------------------


def _peak_db(path: Path, start_s: float, length_s: float) -> float:
    peak = audio_peak(path, start_s, length_s)
    return 20 * math.log10(peak) if peak > 0 else -math.inf


def test_synth_audio_levels_follow_the_contract(synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=9000,
        size=(64, 36),
        beeps_ms=[(3000, 4000)],
        speech_like_ms=[(6000, 7000)],
        silence_ms=[(7500, 8500)],
    )
    path = synth(spec)
    assert _peak_db(path, 0.5, 2.0) == pytest.approx(-20.0, abs=1.5)  # tone at 0.1
    assert _peak_db(path, 3.2, 0.6) == pytest.approx(-0.9, abs=1.5)  # beep at 0.9
    # seeded white noise at 0.5 peaks at -1.06 dB (AAC overshoot included): about 3 dB of margin
    assert -14.0 < _peak_db(path, 6.2, 0.6) < 2.0
    assert _peak_db(path, 7.7, 0.6) < -70.0  # exact silence
