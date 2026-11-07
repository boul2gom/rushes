"""Episode and post files as strict Pydantic models (docs/plans/00-master.md section 5.6)."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rushes.core.types import SoundEvent
from rushes.fsutil import atomic_path

type RemovedKind = Literal["black", "silence", "intro", "outro", "title_card"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def write_json(model: BaseModel, path: Path) -> None:
    """Atomic, UTF-8, indent 1: the same model always gives the same bytes."""
    with atomic_path(path) as tmp:
        tmp.write_text(model.model_dump_json(indent=1) + "\n", encoding="utf-8")


def read_json[M: BaseModel](model: type[M], path: Path) -> M:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


class CleanFile(_Strict):
    """Episode cleaning: `kept` is in source time, clean.mp4 time 0 is `kept[0]`."""

    version: Literal[1]
    episode_id: int
    source_duration_ms: int
    kept: tuple[int, int]
    removed: list[tuple[int, int, RemovedKind]]
    span: tuple[int, int] | None = None  # child episode of a split compilation (task 11)


class Shot(_Strict):
    """A shot in clean.mp4 time (0 = start of clean.mp4); `id` is its index in the file.

    `keyframe_ms` is the middle of the shot, except for the last shot when the sound outlasts the
    picture: it is then held back to the last frame, so it may be before the middle.
    """

    id: int
    start_ms: int
    end_ms: int
    keyframe_ms: int
    motion: float
    loudness_lufs: float | None
    speech_ratio: float
    phash: str


class ScenesFile(_Strict):
    version: Literal[1]
    episode_id: int
    duration_ms: int
    detector: dict[str, float]
    shots: list[Shot]


class LoudPoint(_Strict):
    """Short-term loudness every 100 ms. `t_ms` is the END of the 3 s window the value covers."""

    t_ms: int
    lufs: float


class AudioFile(_Strict):
    version: Literal[1]
    episode_id: int
    integrated_lufs: float
    loudness: list[LoudPoint]
    speech: list[tuple[int, int]]
    events: list[SoundEvent] | None = None  # task 12b; None = not analyzed
