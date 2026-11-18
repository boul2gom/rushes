"""CED-mini sound tagger on sherpa-onnx (R39: local, files from the manifest, no network).

`sherpa_onnx` is imported when the tagger is built, never when this module is. The model tags
fixed windows (`window_ms`, every `hop_ms`); `window_tags` gives the raw AudioSet labels of each,
`tag` groups them into the project's events (`analyze.sounds.group_events`).
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rushes.core.types import SoundEvent
from rushes.media.audio import SAMPLE_RATE

MODEL_FILE = "model.int8.onnx"
LABELS_FILE = "class_labels_indices.csv"

type WindowTags = list[tuple[int, list[tuple[str, float]]]]


class CedTagger:
    """`SoundTagger`: AudioSet labels of sliding windows, grouped into the project's events."""

    def __init__(
        self,
        model_dir: Path,
        *,
        threads: int = 4,
        window_ms: int = 1000,
        hop_ms: int = 500,
        top_k: int = 5,
    ) -> None:
        import sherpa_onnx

        if window_ms < 1 or hop_ms < 1 or top_k < 1:
            raise ValueError("window_ms, hop_ms et top_k doivent être au moins 1")
        model = sherpa_onnx.AudioTaggingModelConfig(
            ced=str(model_dir / MODEL_FILE), num_threads=threads
        )
        config = sherpa_onnx.AudioTaggingConfig(
            model=model, labels=str(model_dir / LABELS_FILE), top_k=top_k
        )
        self._tagger: Any = sherpa_onnx.AudioTagging(config)
        self._window_ms = window_ms
        self._hop_ms = hop_ms
        self._top_k = top_k

    def window_tags(self, pcm16k: NDArray[np.float32]) -> WindowTags:
        """(start_ms, [(label, score)]) per window, best score first, `top_k` labels at most.

        Windows start every `hop_ms`; a tail the last one does not reach gets a window ending at
        the end of the audio; audio shorter than one window is tagged as a single window.
        """
        size = SAMPLE_RATE * self._window_ms // 1000
        hop = SAMPLE_RATE * self._hop_ms // 1000
        count = len(pcm16k)
        if count == 0:
            return []
        starts = list(range(0, max(count - size, 0) + 1, hop))
        if starts[-1] + size < count:
            starts.append(count - size)
        found: WindowTags = []
        for start in starts:
            stream = self._tagger.create_stream()
            stream.accept_waveform(SAMPLE_RATE, pcm16k[start : start + size])
            events = self._tagger.compute(stream, self._top_k)
            tags = [(str(e.name), round(float(e.prob), 4)) for e in events]  # float32 noise out
            found.append((start * 1000 // SAMPLE_RATE, tags))
        return found

    def tag(self, pcm16k: NDArray[np.float32]) -> list[SoundEvent]:
        # imported here: it keeps sqlalchemy and the step's context out of this model module
        from rushes.analyze.sounds import group_events

        events = group_events(self.window_tags(pcm16k), window_ms=self._window_ms)
        length_ms = len(pcm16k) * 1000 // SAMPLE_RATE  # a window may reach past a short audio
        return [
            replace(e, end_ms=min(e.end_ms, length_ms)) for e in events if e.start_ms < length_ms
        ]
