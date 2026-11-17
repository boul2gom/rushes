"""Minimum spacing between requests (R18), shared by every call of a client."""

import threading
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        min_interval_s: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = min_interval_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Return once `min_interval_s` has passed since the previous call returned."""
        with self._lock:
            if self._last is not None:
                remaining = self._interval - (self._monotonic() - self._last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._monotonic()
