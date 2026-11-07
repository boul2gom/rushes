"""Frame access: small gray samples for motion, and key images."""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushes.errors import MediaError
from rushes.fsutil import atomic_path
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe


def sample_gray(
    path: Path, *, fps: float, width: int, tmp_dir: Path | None = None
) -> Iterator[tuple[int, NDArray[np.uint8]]]:
    """Gray images sampled `fps` times a second, `width` px wide: (t_ms, image).

    The raw frames go through a temporary file under `tmp_dir` (default: the system one).
    """
    info = probe(path)
    height = max(2, round(width * info.height / info.width / 2) * 2)
    size = width * height
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir, prefix="frames-") as folder:
        raw = Path(folder) / "frames.gray"
        video_filter = f"fps={fps},scale={width}:{height},format=gray"
        run_ffmpeg(["-i", path, "-an", "-vf", video_filter, "-f", "rawvideo", raw])
        with raw.open("rb") as handle:
            index = 0
            while block := handle.read(size):
                if len(block) < size:
                    break
                image = np.frombuffer(block, dtype=np.uint8).reshape(height, width)
                yield round(index * 1000 / fps), image
                index += 1


def extract_frame(path: Path, t_ms: int, out: Path, *, width: int = 512) -> None:
    """One JPEG of the frame at `t_ms`, at most `width` px wide; MediaError if there is none."""
    scale = f"scale=min({width}\\,iw):-2,format=yuvj420p"
    with atomic_path(out) as tmp:
        seek = f"{t_ms / 1000:.3f}"
        args: list[str | Path] = ["-ss", seek, "-i", path]
        args += ["-an", "-frames:v", "1", "-vf", scale, "-q:v", "3"]
        run_ffmpeg([*args, "-f", "mjpeg", tmp])
        if not tmp.is_file() or tmp.stat().st_size == 0:  # ffmpeg exits 0 past the last frame
            raise MediaError(f"aucune image à {t_ms} ms dans {path.name}")
