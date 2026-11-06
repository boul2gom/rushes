"""Atomic file production: write to a sibling temporary path, rename at the end."""

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def atomic_path(final: Path) -> Iterator[Path]:
    """Yield `.<name>.tmp-<pid>` next to `final`; rename it onto `final` when the block succeeds.

    On any error the temporary file (or folder) is removed and `final` is left untouched.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{final.name}.tmp-{os.getpid()}")
    _remove(tmp)
    try:
        yield tmp
        if tmp.is_dir() and final.is_dir():
            shutil.rmtree(final)  # a folder cannot be renamed onto a non-empty one
        os.replace(tmp, final)
    except BaseException:
        _remove(tmp)
        raise
