"""Audio fingerprint of a series theme (task 11, C50)."""

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
import scipy.signal
from numpy.typing import NDArray

from fixtures.generator import Synth, SynthSpec, make_theme
from rushes.media import fingerprint
from rushes.media.audio import SAMPLE_RATE, decode_pcm16k
from rushes.media.fingerprint import (
    band_spectrogram,
    slice_correlation,
    theme_correlation,
    theme_matches,
)

TINY = (160, 90)


def _theme(tmp_path: Path) -> NDArray[np.float32]:
    return decode_pcm16k(make_theme(tmp_path / "theme.wav"))


def _noise(seconds: float, seed: int = 1, level: float = 0.05) -> NDArray[np.float32]:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def _put(track: NDArray[np.float32], theme: NDArray[np.float32], at_ms: int) -> None:
    start = at_ms * SAMPLE_RATE // 1000
    track[start : start + theme.size] += theme[: track.size - start]


def _near(found: list[tuple[int, float]], expected: list[int], tolerance_ms: int = 100) -> bool:
    return len(found) == len(expected) and all(
        abs(start - want) <= tolerance_ms for (start, _), want in zip(found, expected, strict=True)
    )


# --- band_spectrogram -------------------------------------------------------------------------


def test_band_spectrogram_shape_zero_mean_and_float32() -> None:
    spec = band_spectrogram(_noise(5.0), hop_ms=50, bands=48)
    assert spec.dtype == np.float32
    assert spec.shape == (48, (5 * SAMPLE_RATE - 1024) // 800 + 1)
    assert np.abs(spec.mean(axis=1)).max() < 1e-3  # the mean of each band is removed
    assert np.isfinite(spec).all()


def test_band_spectrogram_of_too_few_samples_has_no_frame() -> None:
    assert band_spectrogram(np.zeros(500, dtype=np.float32)).shape == (48, 0)
    assert band_spectrogram(np.zeros(0, dtype=np.float32)).shape == (48, 0)


def test_band_spectrogram_blocks_equal_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    pcm = _noise(8.0, seed=3)
    whole = band_spectrogram(pcm)
    monkeypatch.setattr(fingerprint, "BLOCK_FRAMES", 7)  # many small blocks
    assert np.allclose(band_spectrogram(pcm), whole, atol=1e-4)


def test_band_spectrogram_uses_scipy_stft_with_the_hop_as_step() -> None:
    pcm = _noise(2.0, seed=4)
    _, _, z = scipy.signal.stft(
        pcm * np.float32(32768), SAMPLE_RATE, nperseg=1024, noverlap=1024 - 800,
        boundary=None, padded=False,
    )  # fmt: skip
    assert band_spectrogram(pcm).shape[1] == z.shape[1]


def test_every_band_has_a_bin_even_below_the_fft_resolution() -> None:
    weights = fingerprint._band_matrix(48)
    assert weights.shape == (48, 513)
    assert np.allclose(weights.sum(axis=1), 1.0)  # each band averages at least one bin


def test_band_spectrogram_rejects_a_hop_longer_than_the_window() -> None:
    with pytest.raises(ValueError):
        band_spectrogram(_noise(1.0), hop_ms=80)


# --- theme_matches ----------------------------------------------------------------------------


def test_theme_matches_finds_inserted_theme(synth: Synth, tmp_path: Path) -> None:
    video = synth(SynthSpec(duration_ms=90_000, size=TINY, fps=10, theme_ms=[10_000, 70_000]))
    found = theme_matches(decode_pcm16k(video), _theme(tmp_path))
    assert _near(found, [10_000, 70_000])
    assert all(corr > 0.9 for _, corr in found)  # through AAC and a resampling


def test_theme_matches_noise_only_finds_nothing(synth: Synth, tmp_path: Path) -> None:
    assert theme_matches(_noise(60.0), _theme(tmp_path)) == []
    plain = synth(SynthSpec(duration_ms=40_000, size=TINY, fps=10, beeps_ms=[(5000, 6000)]))
    assert theme_matches(decode_pcm16k(plain), _theme(tmp_path)) == []


def test_theme_matches_survives_additive_noise(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(120.0, seed=7, level=0.05)  # about 10 dB under the theme
    _put(track, theme, 20_000)
    _put(track, theme, 80_000)
    assert _near(theme_matches(track, theme), [20_000, 80_000])


def test_theme_matches_two_positions_in_a_long_compilation(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(30 * 60.0, seed=8)
    _put(track, theme, 612_300)
    _put(track, theme, 1_203_450)
    found = theme_matches(track, theme)
    assert _near(found, [612_300, 1_203_450])


def test_theme_matches_at_the_very_start_and_the_very_end(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(40.0, seed=9)
    _put(track, theme, 0)
    _put(track, theme, 40_000 - 6000)  # the last offset: the theme ends with the track
    assert _near(theme_matches(track, theme), [0, 34_000])


def test_theme_matches_peaks_are_one_theme_length_apart(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(60.0, seed=10)
    _put(track, theme, 10_000)
    _put(track, theme, 16_000)  # back to back: both count
    assert _near(theme_matches(track, theme), [10_000, 16_000])
    near = theme_matches(track, theme, threshold=0.2)  # a low bar finds shoulders, never closer
    assert all(b[0] - a[0] >= 6000 for a, b in pairwise(near))


def test_theme_matches_threshold_is_inclusive_and_respected(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(30.0, seed=11)
    _put(track, theme, 10_000)
    [(_, corr)] = theme_matches(track, theme)
    assert theme_matches(track, theme, threshold=corr) != []
    assert theme_matches(track, theme, threshold=min(1.0, corr + 0.01)) == []


def test_theme_matches_degenerate_inputs_never_raise(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    silence = np.zeros(SAMPLE_RATE * 30, dtype=np.float32)
    assert theme_matches(silence, theme) == []  # silent track
    assert theme_matches(_noise(30.0), silence[: SAMPLE_RATE * 6]) == []  # silent theme
    assert theme_matches(_noise(3.0), theme) == []  # theme longer than the track
    assert theme_matches(_noise(30.0), theme[:900]) == []  # shorter than one window
    assert theme_matches(_noise(30.0), np.zeros(0, dtype=np.float32)) == []
    assert theme_matches(np.zeros(0, dtype=np.float32), theme) == []
    loud = np.ones(SAMPLE_RATE * 30, dtype=np.float32)  # constant: every band is flat
    assert theme_matches(loud, theme) == []


def test_theme_correlation_is_finite_and_bounded_on_flat_data(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = np.concatenate(
        [np.zeros(SAMPLE_RATE * 10), _noise(10.0, seed=12), np.full(SAMPLE_RATE * 10, np.nan)]
    ).astype(np.float32)  # silence, noise, a decoder glitch
    corr = theme_correlation(track, theme)
    assert corr.dtype == np.float32 and corr.size > 0
    assert np.isfinite(corr).all() and np.abs(corr).max() <= 1.0
    flat = theme_correlation(np.zeros(SAMPLE_RATE * 20, dtype=np.float32), theme)
    assert (flat == 0).all()  # zero-variance windows correlate 0, not NaN


def test_theme_correlation_offsets_are_frames(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(20.0, seed=13)
    _put(track, theme, 5000)
    corr = theme_correlation(track, theme, hop_ms=50)
    frames = band_spectrogram(track).shape[1] - band_spectrogram(theme).shape[1] + 1
    assert corr.size == frames
    assert int(np.argmax(corr)) * 50 == 5000


def test_the_largest_hop_the_settings_allow_works(tmp_path: Path) -> None:
    # 63 ms = 1008 samples, just under the 1024-sample window (64 ms is refused by the settings)
    theme = _theme(tmp_path)
    track = _noise(30.0, seed=14)
    _put(track, theme, 10_000)
    assert _near(theme_matches(track, theme, hop_ms=63), [10_000], tolerance_ms=63)


# --- a theme cut by the end of the track -------------------------------------------------------


def test_a_theme_cut_by_the_end_of_the_track_still_matches(tmp_path: Path) -> None:
    theme = _theme(tmp_path)  # 6 s
    track = _noise(30.0, seed=15)
    cut = theme[: int(4.5 * SAMPLE_RATE)]  # 75 % of it plays before the file ends
    track[-cut.size :] += cut
    found = theme_matches(track, theme)
    assert _near(found, [25_500]) and found[0][1] > 0.6
    assert theme_matches(track, theme, min_overlap=1.0) == []  # only whole themes: not found


def test_a_theme_cut_below_the_overlap_is_not_found(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(30.0, seed=16)
    cut = theme[: int(3.0 * SAMPLE_RATE)]  # only half of it
    track[-cut.size :] += cut
    assert theme_matches(track, theme) == []


def test_noise_silence_or_music_at_the_end_of_the_track_is_not_a_cut_theme(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    noise = _noise(40.0, seed=17)
    silent_end = np.concatenate([_noise(30.0, seed=18), np.zeros(10 * SAMPLE_RATE, np.float32)])
    loud_end = np.concatenate([_noise(30.0, seed=19), _noise(10.0, seed=20, level=0.5)])
    steady_end = np.concatenate([_noise(30.0, seed=21), np.full(10 * SAMPLE_RATE, 0.3, np.float32)])
    for track in (noise, silent_end, loud_end, steady_end):
        assert theme_matches(track.astype(np.float32), theme) == []
        assert np.isfinite(
            theme_correlation(track.astype(np.float32), theme, min_overlap=0.7)
        ).all()


def test_the_whole_theme_at_the_very_end_is_unchanged_by_partial_offsets(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(30.0, seed=22)
    _put(track, theme, 24_000)
    assert _near(theme_matches(track, theme), [24_000])
    assert (
        theme_correlation(track, theme).size
        == band_spectrogram(track).shape[1] - band_spectrogram(theme).shape[1] + 1
    )


def test_track_and_theme_spectrograms_can_be_given(tmp_path: Path) -> None:
    theme = _theme(tmp_path)
    track = _noise(30.0, seed=23)
    _put(track, theme, 10_000)
    ready = theme_matches(
        track, theme, track_spec=band_spectrogram(track), theme_spec=band_spectrogram(theme)
    )
    assert ready == theme_matches(track, theme)


def test_slice_correlation_is_one_for_itself_and_zero_for_flat_or_mismatched_slices() -> None:
    spec = band_spectrogram(_noise(5.0, seed=24))[:, :40]
    assert slice_correlation(spec, spec) == pytest.approx(1.0)
    assert slice_correlation(spec, -spec) == pytest.approx(-1.0)
    assert slice_correlation(np.zeros_like(spec), spec) == 0.0
    assert slice_correlation(spec, spec[:, :10]) == 0.0
    assert slice_correlation(spec[:, :1], spec[:, :1]) == 0.0
