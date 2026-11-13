"""Opening and closing credits found by comparing frames with the series' reference images."""

import tempfile
from collections.abc import Sequence
from pathlib import Path

import imagehash
from PIL import Image

from rushes.core.intervals import Span, merge
from rushes.core.phash import phash_distance
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe


def _hash(image: Path) -> str:
    with Image.open(image) as picture:
        return str(imagehash.phash(picture.convert("L")))


def _window_matches(
    video: Path,
    start_ms: int,
    length_ms: int,
    ref_hashes: Sequence[str],
    *,
    fps: int,
    max_distance: int,
    work: Path,
) -> list[Span]:
    pattern = work / f"w{start_ms}_%05d.png"
    seek = ["-ss", f"{start_ms / 1000:.3f}", "-t", f"{length_ms / 1000:.3f}"]
    run_ffmpeg([*seek, "-i", video, "-an", "-vf", f"fps={fps},scale=128:-2", pattern])
    spans: list[Span] = []
    for index, frame in enumerate(sorted(work.glob(f"w{start_ms}_*.png"))):
        digest = _hash(frame)
        if any(phash_distance(digest, ref) <= max_distance for ref in ref_hashes):
            begin = start_ms + index * 1000 // fps
            spans.append((begin, begin + 1000 // fps))
    return spans


def match_reference_frames(
    video: Path,
    refs: Sequence[Path],
    *,
    window_ms: int = 90_000,
    fps: int = 2,
    max_distance: int = 12,
    tmp_dir: Path | None = None,
    duration_ms: int | None = None,
    span: Span | None = None,
    windows: Sequence[Span] | None = None,
) -> list[Span]:
    """Spans where the picture looks like a reference image, in the first and last `window_ms`.

    Frames are sampled at `fps`; two matches separated by one missed sample are one span. `span`
    (ms of the file) moves those two windows to the edges of that part (a child episode); `windows`
    replaces them with the given ones (where a theme was heard). Spans are in file time.
    """
    if not refs:
        return []
    duration = probe(video).duration_ms if duration_ms is None else duration_ms
    ref_hashes = [_hash(ref) for ref in refs]
    if windows is not None:
        limit = duration
        todo = {(a, min(b, duration) - a) for a, b in windows if a < min(b, duration)}
    else:
        lo, limit = span if span is not None else (0, duration)
        length = limit - lo
        todo = {
            (lo, min(window_ms, length)),
            (lo + max(0, length - window_ms), min(window_ms, length)),
        }
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    found: list[Span] = []
    with tempfile.TemporaryDirectory(dir=tmp_dir, prefix="intro-") as folder:
        for start, length in sorted(todo):
            found += _window_matches(
                video,
                start,
                length,
                ref_hashes,
                fps=fps,
                max_distance=max_distance,
                work=Path(folder),
            )
    clipped = [(s, min(e, limit)) for s, e in found if s < limit]
    return merge(clipped, gap_ms=1000 // fps)
