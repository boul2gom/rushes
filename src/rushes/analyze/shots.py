"""Shot detection with PySceneDetect (parameters fixed explicitly, spec)."""

from pathlib import Path

from rushes.core.intervals import Span
from rushes.errors import MediaError
from rushes.media.probe import probe

ADAPTIVE_THRESHOLD = 3.0
MIN_SCENE_LEN = 15
WINDOW_WIDTH = 2
MIN_CONTENT_VAL = 15.0


def detect_shots(
    path: Path,
    *,
    adaptive_threshold: float = ADAPTIVE_THRESHOLD,
    min_scene_len: int = MIN_SCENE_LEN,
    window_width: int = WINDOW_WIDTH,
    min_content_val: float = MIN_CONTENT_VAL,
) -> list[Span]:
    """Shots as (start_ms, end_ms); no cut found gives one shot over the whole video."""
    from scenedetect import AdaptiveDetector, detect

    detector = AdaptiveDetector(
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_scene_len,
        window_width=window_width,
        min_content_val=min_content_val,
    )
    try:
        scenes = detect(str(path), detector, backend="opencv")
    except Exception as exc:
        raise MediaError(f"détection des plans impossible : {path.name} ({exc})") from exc
    spans = [(round(start.seconds * 1000), round(end.seconds * 1000)) for start, end in scenes]
    spans = [(s, e) for s, e in spans if e > s]
    return spans or [(0, probe(path).duration_ms)]
