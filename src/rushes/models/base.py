"""Protocols of the local models (docs/plans/00-master.md section 5.5)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from rushes.core.intervals import Span
from rushes.core.types import SoundEvent, Word

__all__ = [
    "Box",
    "FaceDetector",
    "ImageEncoder",
    "SoundEvent",
    "SoundTagger",
    "SpeechSynth",
    "TextGenerator",
    "Transcriber",
    "Vad",
    "Word",
]


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int
    score: float


class Vad(Protocol):
    def speech(self, pcm16k: NDArray[np.float32]) -> list[Span]: ...


class Transcriber(Protocol):
    def transcribe(self, wav16k: Path, *, language: str, clips: Sequence[Span]) -> list[Word]: ...


class SoundTagger(Protocol):
    def tag(self, pcm16k: NDArray[np.float32]) -> list[SoundEvent]: ...


class ImageEncoder(Protocol):
    dim: int

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]: ...  # L2-normalized
    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]: ...


class FaceDetector(Protocol):
    def detect(self, bgr: NDArray[np.uint8]) -> list[Box]: ...


class TextGenerator(Protocol):
    def generate[M: BaseModel](
        self, system: str, user: str, schema: type[M], *, timeout_s: float
    ) -> M | None: ...


class SpeechSynth(Protocol):
    def synthesize(self, text: str, *, language: str, out_wav: Path) -> int: ...  # duration in ms
