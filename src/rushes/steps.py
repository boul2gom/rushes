"""Registry of pipeline steps: name -> factory, and the default run order (filled by each task)."""

from collections.abc import Callable
from typing import Any

from rushes.runner import Step

STEPS: dict[str, Callable[[], Step[Any]]] = {}
RUN_ORDER: tuple[str, ...] = ()
