"""Heavy-work lock and express-lane intent (flock on a local volume, never NFS).

A lock is released when the process dies; the files are never removed. Two `open()` of the same
file block each other even inside one process, so whoever holds a lock hands it down (see
`run_pipeline(lock=..., yield_to_express=False)`) instead of opening it again.
"""

import fcntl
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rushes.errors import LockBusy
from rushes.paths import Paths

HEAVY = "heavy.lock"
EXPRESS = "express.lock"
_POLL_S = 0.05


def open_lock(paths: Paths, name: str) -> int:
    """File descriptor of the lock file `name` (created if needed); the caller takes the flock."""
    paths.locks.mkdir(parents=True, exist_ok=True)
    return os.open(paths.locks / name, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)


def try_lock(paths: Paths, name: str) -> int | None:
    """Take the lock `name` without waiting: its descriptor (close it to release), None if busy."""
    fd = open_lock(paths, name)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _acquire(fd: int, *, wait: bool, timeout_s: float | None) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except BlockingIOError:
        if not wait:
            raise _busy() from None
    if timeout_s is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + timeout_s
    while True:
        time.sleep(_POLL_S)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise _busy() from None


def _busy() -> LockBusy:
    return LockBusy(
        "le verrou lourd est tenu par un autre run",
        hint="Attends la fin du run en cours, ou relance sans --no-wait pour patienter.",
    )


class HeavyLock:
    """The heavy lock, held. Handed to the runner so that it can yield to the express lane."""

    def __init__(self, paths: Paths, fd: int) -> None:
        self._paths = paths
        self._fd = fd

    def yield_if_express_waiting(self, *, before_yield: Callable[[], None]) -> bool:
        """Between two items: if the express lane waits, release, let it work, take the lock back.

        `before_yield` (ctx.models.release) runs before the lock is given away. Returns True when
        the lock changed hands.
        """
        express = open_lock(self._paths, EXPRESS)
        try:
            try:
                fcntl.flock(express, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                before_yield()
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                fcntl.flock(express, fcntl.LOCK_EX)  # returns when the express lane is over
                fcntl.flock(express, fcntl.LOCK_UN)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
                return True
            fcntl.flock(express, fcntl.LOCK_UN)  # nobody waits
            return False
        finally:
            os.close(express)


@contextmanager
def heavy_lock(paths: Paths, *, wait: bool, timeout_s: float | None = None) -> Iterator[HeavyLock]:
    """Take the heavy lock. `wait=False` (or an expired `timeout_s`) raises LockBusy (code 3)."""
    fd = open_lock(paths, HEAVY)
    try:
        _acquire(fd, wait=wait, timeout_s=timeout_s)
        try:
            yield HeavyLock(paths, fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def express_intent(paths: Paths) -> Iterator[None]:
    """The express lane announces that it waits for (then holds) the heavy lock."""
    fd = open_lock(paths, EXPRESS)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
