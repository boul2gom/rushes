"""Speech spans from a voice activity detector."""

import numpy as np
from numpy.typing import NDArray

from rushes.core.intervals import Span, merge
from rushes.models.base import Vad


def speech_spans(vad: Vad, pcm16k: NDArray[np.float32]) -> list[Span]:
    """Sorted, merged, non-empty speech spans (ms) of 16 kHz mono samples."""
    return merge((s, e) for s, e in vad.speech(pcm16k) if e > s)
