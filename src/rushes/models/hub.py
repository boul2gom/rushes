"""Local model hub (docs/plans/00-master.md section 5.5).

Models load on first use and stay loaded until `release` (R19: once per step, never per item).
Heavy ones (transcriber, clip, llm) never coexist: loading one drops the previous one. The factory
of a model comes from `factories` (tests) or `DEFAULT_FACTORIES`; a model whose task has not
landed yet raises NotImplementedError naming it.
"""

import gc
from collections.abc import Callable, Mapping
from typing import cast

from rushes.config import Settings
from rushes.models.base import (
    FaceDetector,
    ImageEncoder,
    SoundTagger,
    SpeechSynth,
    TextGenerator,
    Transcriber,
    Vad,
)
from rushes.paths import Paths

HEAVY = frozenset({"transcriber", "clip", "llm"})


def _silero() -> object:
    from rushes.models.vad_silero import SileroVad

    return SileroVad()


# name -> factory of the real model; filled by the task that brings each model
DEFAULT_FACTORIES: dict[str, Callable[[], object]] = {"vad": _silero}


class ModelHub:
    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        *,
        factories: Mapping[str, Callable[[], object]] | None = None,
    ) -> None:
        self.paths = paths
        self.settings = settings
        self._factories = dict(factories or {})
        self._loaded: dict[str, object] = {}
        self._loads: dict[str, int] = {}

    def _factory(self, name: str) -> Callable[[], object] | None:
        return self._factories.get(name) or DEFAULT_FACTORIES.get(name)

    def get(self, name: str) -> object:
        """The model `name`, loaded on first use (R19: once until released)."""
        if name not in self._loaded:
            factory = self._factory(name)
            if factory is None:
                raise KeyError(name)
            if name in HEAVY:  # one heavy model at a time
                for other in HEAVY & self._loaded.keys():
                    del self._loaded[other]
                gc.collect()
            self._loaded[name] = factory()
            self._loads[name] = self._loads.get(name, 0) + 1
        return self._loaded[name]

    def _model(self, name: str, task: str) -> object:
        if self._factory(name) is None:
            raise NotImplementedError(f"tâche {task}")
        return self.get(name)

    def vad(self) -> Vad:
        return cast(Vad, self._model("vad", "5"))

    def faces(self) -> FaceDetector:
        return cast(FaceDetector, self._model("faces", "34"))

    def sounds(self) -> SoundTagger:
        return cast(SoundTagger, self._model("sounds", "12b"))

    def tts(self) -> SpeechSynth | None:
        return cast(SpeechSynth | None, self._model("tts", "35"))

    def transcriber(self) -> Transcriber:
        return cast(Transcriber, self._model("transcriber", "12c"))

    def clip(self) -> ImageEncoder:
        return cast(ImageEncoder, self._model("clip", "12a"))

    def llm(self) -> TextGenerator | None:
        return cast(TextGenerator | None, self._model("llm", "14"))

    def loads(self) -> Mapping[str, int]:
        """How many times each model was loaded."""
        return dict(self._loads)

    def release(self) -> None:
        """Drop every loaded model (memory budget); the next access reloads."""
        self._loaded.clear()
        gc.collect()
