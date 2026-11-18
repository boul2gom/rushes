"""Local model hub (docs/plans/00-master.md section 5.5).

Models load on first use and stay loaded until `release` (R19: once per step, never per item).
Heavy ones (transcriber, clip, llm) never coexist: loading one drops the previous one. The factory
of a model comes from `factories` (tests), `DEFAULT_FACTORIES` or `BOUND_FACTORIES` (real models
that need the hub's paths and settings); a model whose task has not landed yet raises
NotImplementedError naming it.
"""

import csv
import gc
from collections.abc import Callable, Mapping
from functools import partial
from typing import cast

from rushes.config import Settings
from rushes.errors import RushesError
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


def _clip(paths: Paths, settings: Settings) -> object:
    """OpenCLIP on the weights of the manifest (`rushes models fetch --only clip`)."""
    from rushes.models.clip import PRETRAINED, OpenClipEncoder
    from rushes.models.fetch import load_manifest

    if settings.models.clip_pretrained != PRETRAINED:  # the weights are the manifest's only entry
        raise RushesError(
            f"models.clip_pretrained = {settings.models.clip_pretrained!r} : "
            "le manifeste ne fournit pas ces poids",
            hint=f"Utilise {PRETRAINED!r} (ou ajoute d'abord ces poids au manifeste).",
        )
    entry = next(e for e in load_manifest() if e.name == "clip")
    weights = paths.cache_models / entry.file
    if not weights.is_file():
        raise RushesError(
            f"poids du modèle clip absents ({entry.file})",
            hint="Lance `rushes models fetch --only clip`.",
        )
    return OpenClipEncoder(weights, batch=settings.analyze.clip_batch)


def _ced(paths: Paths, settings: Settings) -> object:
    """CED-mini on the files of the manifest (`rushes models fetch --only ced`).

    The labels file must name every AudioSet label the project groups: sherpa-onnx returns the
    names as the file spells them, and a name that does not match would silently give no event.
    """
    from rushes.analyze.sounds import AUDIOSET_GROUPS
    from rushes.models.ced import CedTagger
    from rushes.models.fetch import load_manifest

    entries = [e for e in load_manifest() if e.name == "ced"]
    files = [paths.cache_models / e.file for e in entries]
    fetch = "Lance `rushes models fetch --only ced`."
    missing = [e.file for e, path in zip(entries, files, strict=True) if not path.is_file()]
    if missing:
        raise RushesError(f"fichiers du modèle ced absents ({', '.join(missing)})", hint=fetch)
    labels = next((path for path in files if path.suffix == ".csv"), None)
    names: set[str] = set()
    if labels is not None:
        try:
            with labels.open(encoding="utf-8", newline="") as handle:
                names = {row["display_name"] for row in csv.DictReader(handle)}
        except OSError, UnicodeDecodeError, KeyError, csv.Error:
            pass  # reported below as missing labels
    absent = sorted(set(AUDIOSET_GROUPS) - names)
    if absent:
        raise RushesError(
            f"étiquettes absentes du fichier de labels ced : {', '.join(absent)}",
            hint="Le fichier est inattendu : relance `rushes models fetch --only ced`.",
        )
    return CedTagger(files[0].parent)


# name -> factory of the real model; filled by the task that brings each model
DEFAULT_FACTORIES: dict[str, Callable[[], object]] = {"vad": _silero}
# same, for a model that needs the hub's paths and settings (built with them, see `_factory`)
BOUND_FACTORIES: dict[str, Callable[[Paths, Settings], object]] = {"clip": _clip, "sounds": _ced}


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
        factory = self._factories.get(name) or DEFAULT_FACTORIES.get(name)
        if factory is None and name in BOUND_FACTORIES:
            return partial(BOUND_FACTORIES[name], self.paths, self.settings)
        return factory

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
