"""Exit codes and the errors that carry them (docs/plans/00-master.md section 5.4)."""

from typing import ClassVar

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID_CONFIG = 2
EXIT_LOCK_BUSY = 3
EXIT_PARTIAL = 4
EXIT_SOURCE_UNAVAILABLE = 5


class RushesError(Exception):
    """An expected failure: a short French message, a hint, and the exit code of the command."""

    exit_code: ClassVar[int] = EXIT_ERROR
    hint: str | None = None

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message)
        if hint is not None:
            self.hint = hint


class LockBusy(RushesError):
    exit_code = EXIT_LOCK_BUSY


class SourceUnavailable(RushesError):
    exit_code = EXIT_SOURCE_UNAVAILABLE


class MediaError(RushesError):
    pass
