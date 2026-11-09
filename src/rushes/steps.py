"""Registry of pipeline steps: name -> factory, and the default run order (filled by each task)."""

from collections.abc import Callable
from typing import Any

from rushes.analyze.analyze import AnalyzeStep
from rushes.approve.approve import ApproveStep
from rushes.assign.assign import AssignStep
from rushes.clean.clean import CleanStep
from rushes.cut.parts import CutStep
from rushes.ingest.ingest import IngestStep
from rushes.meta.meta import MetaStep
from rushes.package.outbox import PackageStep
from rushes.render.render import RenderStep
from rushes.runner import Step

STEPS: dict[str, Callable[[], Step[Any]]] = {
    "ingest": IngestStep,
    "clean": CleanStep,
    "analyze": AnalyzeStep,
    "cut": CutStep,
    "approve": ApproveStep,
    "assign": AssignStep,
    "render": RenderStep,
    "meta": MetaStep,
    "package": PackageStep,
}
RUN_ORDER: tuple[str, ...] = (
    "ingest",
    "clean",
    "analyze",
    "cut",
    "approve",
    "assign",
    "render",
    "meta",
    "package",
)
