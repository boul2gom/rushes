"""Registry of pipeline steps: name -> factory, and the default run order (filled by each task)."""

from collections.abc import Callable
from typing import Any

from rushes.analyze.analyze import AnalyzeStep
from rushes.clean.clean import CleanStep
from rushes.cut.parts import CutStep
from rushes.ingest.ingest import IngestStep
from rushes.runner import Step

STEPS: dict[str, Callable[[], Step[Any]]] = {
    "ingest": IngestStep,
    "clean": CleanStep,
    "analyze": AnalyzeStep,
    "cut": CutStep,
}
RUN_ORDER: tuple[str, ...] = ("ingest", "clean", "analyze", "cut")
