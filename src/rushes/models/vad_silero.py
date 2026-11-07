"""Silero VAD, ONNX path (R39: local, no network; the weights ship with the silero-vad package)."""

import numpy as np
from numpy.typing import NDArray

from rushes.core.intervals import Span

SAMPLE_RATE = 16_000


class SileroVad:
    def __init__(self) -> None:
        from silero_vad import load_silero_vad  # heavy: only when the model is needed

        self._model = load_silero_vad(onnx=True)

    def speech(self, pcm16k: NDArray[np.float32]) -> list[Span]:
        import torch
        from silero_vad import get_speech_timestamps

        found = get_speech_timestamps(
            torch.from_numpy(np.ascontiguousarray(pcm16k, dtype=np.float32)),
            self._model,
            sampling_rate=SAMPLE_RATE,
            return_seconds=False,
        )
        spans = [
            (round(item["start"] * 1000 / SAMPLE_RATE), round(item["end"] * 1000 / SAMPLE_RATE))
            for item in found
        ]
        return [(start, end) for start, end in spans if end > start]
