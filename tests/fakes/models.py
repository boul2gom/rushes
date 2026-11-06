"""Model hub double: records releases, loads nothing."""

from collections.abc import Mapping


class FakeModelHub:
    def __init__(self, loaded: Mapping[str, int] | None = None) -> None:
        self._loads = dict(loaded or {})
        self.release_calls = 0

    def loads(self) -> Mapping[str, int]:
        return dict(self._loads)

    def release(self) -> None:
        self.release_calls += 1
