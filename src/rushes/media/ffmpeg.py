"""The only way to run ffmpeg: list arguments (no shell), progress, last stderr lines on failure."""

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from rushes.errors import MediaError

_TAIL_LINES = 20


def _percent(line: str, duration_ms: int) -> int | None:
    key, _, value = line.strip().partition("=")
    if key not in ("out_time_us", "out_time_ms"):  # both are in microseconds
        return None
    try:
        done_ms = int(value) // 1000
    except ValueError:
        return None
    return max(0, min(100, done_ms * 100 // duration_ms))


def run_ffmpeg(
    args: Sequence[str | Path],
    *,
    duration_ms: int | None = None,
    on_progress: Callable[[int], None] | None = None,
    threads: int = 4,
) -> str:
    """Run ffmpeg and return its stderr (the detectors print there).

    The last argument is the output: `-threads` is placed right before it. `on_progress` gets the
    percentage done (0 to 100, only when it changes) and needs `duration_ms`, the expected length
    of the output. MediaError carries the last 20 stderr lines.
    """
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise MediaError("ffmpeg introuvable", hint="Installer FFmpeg (ffmpeg dans le PATH).")
    if not args:
        raise ValueError("ffmpeg : aucun argument")
    *head, output = (str(a) for a in args)
    cmd = [binary, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]
    cmd += ["-filter_threads", str(threads), *head, "-threads", str(threads), output]

    last = -1
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err, text=True
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if on_progress is not None and duration_ms:
                    percent = _percent(line, duration_ms)
                    if percent is not None and percent != last:
                        last = percent
                        on_progress(percent)
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        err.seek(0)
        stderr = err.read()
    if code != 0:
        tail = "\n".join(stderr.strip().splitlines()[-_TAIL_LINES:])
        raise MediaError(f"ffmpeg a échoué (code {code})\n{tail}")
    if on_progress is not None and duration_ms and last < 100:
        on_progress(100)
    return stderr
