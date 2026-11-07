"""Model doubles: deterministic stand-ins for the local models (docs/plans/00-master.md 5.5)."""

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from rushes.core.intervals import Span

SAMPLE_RATE = 16_000
WINDOW_MS = 20
MIN_SPEECH_MS = 250
MIN_RMS = 0.1  # brief: 0.25, but white noise at 0.5 has RMS 0.155 once resampled to 16 kHz
MIN_ZERO_CROSSINGS = 0.2


class FakeVad:
    """Speech = 20 ms windows with RMS > 0.1 and zero-crossing rate > 0.2, runs of >= 250 ms.

    Recognizes the generator's `speech_like_ms` (white noise at 0.5) and ignores its 1000 Hz beeps
    (loud but a low crossing rate) and the background tone (RMS 0.07).
    """

    def speech(self, pcm16k: NDArray[np.float32]) -> list[Span]:
        size = SAMPLE_RATE * WINDOW_MS // 1000
        count = len(pcm16k) // size
        spans: list[Span] = []
        run_start: int | None = None
        for index in range(count + 1):
            voiced = False
            if index < count:
                window = pcm16k[index * size : (index + 1) * size].astype(np.float64)
                rms = float(np.sqrt(np.mean(window**2)))
                crossings = float(np.mean(np.signbit(window[1:]) != np.signbit(window[:-1])))
                voiced = rms > MIN_RMS and crossings > MIN_ZERO_CROSSINGS
            if voiced and run_start is None:
                run_start = index
            elif not voiced and run_start is not None:
                start, end = run_start * WINDOW_MS, index * WINDOW_MS
                if end - start >= MIN_SPEECH_MS:
                    spans.append((start, end))
                run_start = None
        return spans


class FakeModelHub:
    """Records releases, loads nothing; `vad()` is the deterministic double."""

    def __init__(self, loaded: Mapping[str, int] | None = None) -> None:
        self._loads = dict(loaded or {})
        self.release_calls = 0
        self._vad: FakeVad | None = None

    def vad(self) -> FakeVad:
        """Loaded once until `release`, like the real hub (R19)."""
        if self._vad is None:
            self._vad = FakeVad()
            self._loads["vad"] = self._loads.get("vad", 0) + 1
        return self._vad

    def loads(self) -> Mapping[str, int]:
        return dict(self._loads)

    def release(self) -> None:
        self.release_calls += 1
        self._vad = None
