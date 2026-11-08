"""Originality modules (R50): `base` (banner, hook, bar, end card); `advanced` is task 35."""

from rushes.config import Originality
from rushes.originality.base import BaseModule, OriginalityModule

__all__ = ["BaseModule", "OriginalityModule", "module_for"]


def module_for(name: Originality) -> OriginalityModule:
    """The module of a post (`posts.originality`, never the config directly)."""
    if name == "base":
        return BaseModule()
    if name == "advanced":
        raise NotImplementedError("tâche 35")
    raise ValueError(f"module d'originalité inconnu : {name!r}")
