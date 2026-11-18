"""Registry of pipeline steps: name -> factory, and the default run order (filled by each task)."""

from collections.abc import Callable
from typing import Any

from rushes.analyze.analyze import AnalyzeStep
from rushes.analyze.sounds import SoundsStep
from rushes.analyze.vision import VisionStep
from rushes.approve.approve import ApproveStep
from rushes.assign.assign import AssignStep
from rushes.clean.clean import CleanStep
from rushes.cut.parts import CutStep
from rushes.discover.discover import DiscoverYoutubeStep
from rushes.ingest.ingest import IngestStep
from rushes.meta.meta import MetaStep
from rushes.package.outbox import PackageStep
from rushes.render.render import RenderStep
from rushes.runner import Step

STEPS: dict[str, Callable[[], Step[Any]]] = {
    "discover_youtube": DiscoverYoutubeStep,
    "ingest": IngestStep,
    "clean": CleanStep,
    "analyze": AnalyzeStep,
    "vision": VisionStep,
    "sounds": SoundsStep,
    "cut": CutStep,
    "approve": ApproveStep,
    "assign": AssignStep,
    "render": RenderStep,
    "meta": MetaStep,
    "package": PackageStep,
}
RUN_ORDER: tuple[str, ...] = (
    "discover_youtube",
    "ingest",
    "clean",
    "analyze",
    "vision",
    "sounds",
    "cut",
    "approve",
    "assign",
    "render",
    "meta",
    "package",
)
