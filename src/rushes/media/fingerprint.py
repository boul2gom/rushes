"""Audio fingerprint of a series theme: log-band spectrogram, then a sliding Pearson correlation.

Everything stays small in memory: the STFT is computed block by block, the correlation one band at
a time with FFTs, so a 70-minute track costs a few tens of megabytes beyond the samples themselves.
"""

import numpy as np
import scipy.signal
from numpy.typing import NDArray

from rushes.media.audio import SAMPLE_RATE

WINDOW = 1024  # STFT window in samples (64 ms at 16 kHz)
LOW_HZ, HIGH_HZ = 100.0, 7000.0
BLOCK_FRAMES = 2048  # STFT frames computed at once (2048 x 513 complex64 = 8 MB)
INT16_SCALE = (
    32768.0  # float samples are put on the int16 scale: log(1 + energy) is then a real log
)
_TINY = 64 * np.finfo(np.float64).eps  # relative rounding bound of a cumulative sum
PARTIAL_OVERLAP = 0.7  # a theme cut by the end of the track still matches from this share of it


def _hop(hop_ms: int) -> int:
    hop = hop_ms * SAMPLE_RATE // 1000
    if not 1 <= hop <= WINDOW - 1:
        raise ValueError(f"hop_ms hors limites : {hop_ms}")
    return hop


def _band_matrix(bands: int) -> NDArray[np.float32]:
    """(bands, bins) averaging weights: log-spaced bands between 100 and 7 000 Hz.

    The lowest bands are narrower than one FFT bin: such a band takes the bin nearest to its centre
    instead of staying empty.
    """
    freqs = np.fft.rfftfreq(WINDOW, 1 / SAMPLE_RATE)
    edges = np.geomspace(LOW_HZ, HIGH_HZ, bands + 1)
    matrix = np.zeros((bands, freqs.size), dtype=np.float32)
    for band in range(bands):
        members = np.flatnonzero((freqs >= edges[band]) & (freqs < edges[band + 1]))
        if members.size == 0:
            centre = float(np.sqrt(edges[band] * edges[band + 1]))
            members = np.array([int(np.argmin(np.abs(freqs - centre)))])
        matrix[band, members] = 1.0 / members.size
    return matrix


def band_spectrogram(
    pcm: NDArray[np.float32], *, hop_ms: int = 50, bands: int = 48
) -> NDArray[np.float32]:
    """`log(1 + energy)` of `bands` log-spaced bands, shape (bands, frames), band means removed.

    Frame k covers samples [k * hop, k * hop + 1024) of the 16 kHz track. Fewer samples than one
    window give no frame (shape (bands, 0)). Non-finite samples count as silence.
    """
    hop = _hop(hop_ms)
    matrix = _band_matrix(bands)
    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    frames = 0 if samples.size < WINDOW else (samples.size - WINDOW) // hop + 1
    out = np.empty((bands, frames), dtype=np.float32)
    for first in range(0, frames, BLOCK_FRAMES):
        count = min(BLOCK_FRAMES, frames - first)
        begin = first * hop
        block = np.nan_to_num(samples[begin : begin + (count - 1) * hop + WINDOW], copy=True)
        block *= np.float32(INT16_SCALE)
        _, _, spectrum = scipy.signal.stft(
            block,
            fs=SAMPLE_RATE,
            window="hann",
            nperseg=WINDOW,
            noverlap=WINDOW - hop,
            boundary=None,
            padded=False,
        )
        energy = spectrum.real**2 + spectrum.imag**2  # (bins, count)
        out[:, first : first + count] = np.log1p(matrix @ energy.astype(np.float32))
    if frames:
        out -= out.mean(axis=1, keepdims=True)
    return out


def _correlate(
    spec_t: NDArray[np.float32], spec_h: NDArray[np.float32], min_overlap: float
) -> NDArray[np.float32]:
    """Sliding Pearson correlation of spectrogram `spec_h` over `spec_t`, one value per offset.

    Offsets run from 0 to where only `min_overlap` of the theme is left on the track (1.0: only
    offsets with the whole theme on the track). Past the end the correlation is taken on the
    overlap, the beginning of the theme against what remains of the track.
    """
    length = spec_h.shape[1]
    frames = spec_t.shape[1]
    shortest = max(2, int(np.ceil(min(max(min_overlap, 0.0), 1.0) * length)))
    offsets = frames - shortest + 1
    if length < 2 or offsets < 1:
        return np.zeros(0, dtype=np.float32)

    start = np.arange(offsets)
    used = np.minimum(length, frames - start)  # frames of the theme that fit from each offset
    numerator = np.zeros(offsets, dtype=np.float64)
    var_track = np.zeros(offsets, dtype=np.float64)
    var_theme = np.zeros(offsets, dtype=np.float64)
    zero = np.zeros(1)
    for band in range(spec_t.shape[0]):
        x = spec_t[band].astype(np.float64)
        h = spec_h[band].astype(np.float64)
        # sums over the window of the track and over the matching beginning of the theme
        sx = np.concatenate((zero, np.cumsum(x)))
        sxx = np.concatenate((zero, np.cumsum(x * x)))
        sh = np.concatenate((zero, np.cumsum(h)))
        shh = np.concatenate((zero, np.cumsum(h * h)))
        w1, w2 = sx[start + used] - sx[start], sxx[start + used] - sxx[start]
        h1, h2 = sh[used], shh[used]
        cross = scipy.signal.correlate(x, h, mode="full", method="fft")[
            length - 1 : length - 1 + offsets
        ]
        numerator += cross - w1 * h1 / used
        band_x = w2 - w1 * w1 / used
        band_h = h2 - h1 * h1 / used
        var_track += np.where(band_x > _TINY * sxx[-1], band_x, 0.0)
        var_theme += np.where(band_h > _TINY * shh[-1], band_h, 0.0)
    denominator = np.sqrt(var_track * var_theme)
    corr = np.zeros(offsets, dtype=np.float64)
    np.divide(numerator, denominator, out=corr, where=denominator > 0.0)
    return np.clip(np.nan_to_num(corr), -1.0, 1.0).astype(np.float32)


def theme_correlation(
    track: NDArray[np.float32],
    theme: NDArray[np.float32],
    *,
    hop_ms: int = 50,
    min_overlap: float = 1.0,
    track_spec: NDArray[np.float32] | None = None,
) -> NDArray[np.float32]:
    """Pearson correlation of the theme slid over the track: one value per frame offset.

    Offset i puts the theme's first frame on frame i of the track, i.e. at `i * hop_ms`. Each band
    is centred inside the window as well, so a flat stretch (silence, a steady tone) correlates 0.
    Always finite, in [-1, 1]; empty when the theme is longer than the track (or has no frame).
    With `min_overlap` under 1 the offsets go on past the point where the theme still fits, down
    to that share of it (a closing generic cut by the end of the file). `track_spec` is the
    spectrogram of the track when the caller has it already.
    """
    spec_t = band_spectrogram(track, hop_ms=hop_ms) if track_spec is None else track_spec
    return _correlate(spec_t, band_spectrogram(theme, hop_ms=hop_ms), min_overlap)


def slice_correlation(a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
    """Pearson correlation of two equal-size spectrogram slices (bands x frames), 0 when flat."""
    if a.shape != b.shape or a.shape[1] < 2:
        return 0.0
    ca = a.astype(np.float64) - a.mean(axis=1, keepdims=True)
    cb = b.astype(np.float64) - b.mean(axis=1, keepdims=True)
    denominator = float(np.sqrt((ca * ca).sum() * (cb * cb).sum()))
    if not denominator > 0.0:
        return 0.0
    return float(np.clip(np.nan_to_num((ca * cb).sum() / denominator), -1.0, 1.0))


def theme_matches(
    track: NDArray[np.float32],
    theme: NDArray[np.float32],
    *,
    hop_ms: int = 50,
    threshold: float = 0.6,
    min_overlap: float = PARTIAL_OVERLAP,
    track_spec: NDArray[np.float32] | None = None,
    theme_spec: NDArray[np.float32] | None = None,
) -> list[tuple[int, float]]:
    """(start in ms, correlation) of each place where the theme plays in the track.

    Peaks at or above `threshold`, at least one theme length apart (the best of two closer ones
    wins). A peak on the very first or last offset counts. A theme cut by the end of the track
    still matches when at least `min_overlap` of it plays (the correlation is then taken on that
    part). A silent or too short theme, or a theme much longer than the track, matches nowhere.
    `track_spec` and `theme_spec` are the spectrograms when the caller has them already.
    """
    spec_t = band_spectrogram(track, hop_ms=hop_ms) if track_spec is None else track_spec
    spec_h = band_spectrogram(theme, hop_ms=hop_ms) if theme_spec is None else theme_spec
    corr = _correlate(spec_t, spec_h, min_overlap)
    if corr.size == 0:
        return []
    # find_peaks ignores the ends of the array: pad them below any threshold
    padded = np.concatenate(([-2.0], corr, [-2.0])).astype(np.float64)
    peaks, _ = scipy.signal.find_peaks(padded, height=threshold, distance=max(spec_h.shape[1], 1))
    return [(int(i - 1) * hop_ms, float(corr[i - 1])) for i in peaks]
