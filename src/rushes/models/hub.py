"""Local model hub (minimal; completed in task 5): load once per step, release before yielding."""

from collections.abc import Callable, Mapping

from rushes.config import Settings
from rushes.paths import Paths


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

    def get(self, name: str) -> object:
        """The model `name`, loaded on first use (R19: once until released)."""
        if name not in self._loaded:
            if name not in self._factories:
                raise KeyError(name)
            self._loaded[name] = self._factories[name]()
            self._loads[name] = self._loads.get(name, 0) + 1
        return self._loaded[name]

    def loads(self) -> Mapping[str, int]:
        """How many times each model was loaded."""
        return dict(self._loads)

    def release(self) -> None:
        """Drop every loaded model (memory budget); the next `get` reloads."""
        self._loaded.clear()
