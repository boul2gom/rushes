"""Time source: always timezone-aware UTC, replaceable in tests."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...  # always timezone-aware UTC


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
