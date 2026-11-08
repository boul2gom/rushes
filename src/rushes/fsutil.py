"""Atomic file production: write to a sibling temporary path, rename at the end."""

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rushes.errors import RushesError


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _temp_name(final: Path) -> Path:
    return final.with_name(f".{final.name}.tmp-{os.getpid()}")


@contextmanager
def atomic_path(final: Path) -> Iterator[Path]:
    """Yield `.<name>.tmp-<pid>` next to `final`; rename it onto `final` when the block succeeds.

    On any error the temporary file (or folder) is removed and `final` is left untouched.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_name(final)
    _remove(tmp)
    try:
        yield tmp
        if tmp.is_dir() and final.is_dir():
            shutil.rmtree(final)  # a folder cannot be renamed onto a non-empty one
        os.replace(tmp, final)
    except BaseException:
        _remove(tmp)
        raise


class Staged:
    """Temporary siblings of several final files, renamed onto them all at once by `commit`."""

    def __init__(self, finals: tuple[Path, ...]) -> None:
        self._committed = False
        self._tmp = {final: _temp_name(final) for final in finals}
        for final, tmp in self._tmp.items():
            final.parent.mkdir(parents=True, exist_ok=True)
            _remove(tmp)

    def path(self, final: Path) -> Path:
        return self._tmp[final]

    def commit(self) -> None:
        """Rename every temporary onto its final (O(1) each); a second call does nothing.

        RushesError, before any rename, when a temporary was never written."""
        if self._committed:
            return
        missing = [tmp.name for tmp in self._tmp.values() if not tmp.exists()]
        if missing:
            raise RushesError(
                f"fichier temporaire absent, rien n'est remplacé : {', '.join(missing)}"
            )
        for final, tmp in self._tmp.items():
            os.replace(tmp, final)
        self._committed = True

    def discard(self) -> None:
        for tmp in self._tmp.values():
            _remove(tmp)


@contextmanager
def staged(*finals: Path) -> Iterator[Staged]:
    """Like `atomic_path` for several files, but the rename is the caller's `commit()`.

    It lets a step rename the files inside its database transaction, once the guard passed.
    Whatever was not committed (no call, or an error) is removed."""
    stage = Staged(finals)
    try:
        yield stage
    finally:
        stage.discard()
