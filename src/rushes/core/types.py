"""Plain data types shared by the schemas and the models (no heavy import)."""

from dataclasses import dataclass
from typing import Literal

Platform = Literal["tiktok", "instagram"]
type SoundLabel = Literal["impact", "explosion", "splash", "scream", "laughter"]


@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    text: str
    prob: float


@dataclass(frozen=True)
class SoundEvent:
    start_ms: int
    end_ms: int
    label: SoundLabel
    score: float
