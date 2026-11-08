"""ffprobe wrapper and file hashing."""

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from rushes.errors import MediaError

_BLOCK = 1 << 20
_TIMEOUT_S = 120
_VFR_TOLERANCE = 0.01


@dataclass(frozen=True)
class ProbeInfo:
    duration_ms: int
    width: int
    height: int
    display_width: int  # width x sample aspect ratio
    fps: Fraction
    vfr: bool
    has_audio: bool
    vcodec: str
    acodec: str | None
    video_duration_ms: int  # the video stream's own end (duration_ms: the longest stream)


def _fraction(value: object) -> Fraction | None:
    """'30000/1001' -> Fraction; '0/0', 'N/A' or garbage -> None."""
    if not isinstance(value, str) or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        result = Fraction(int(num), int(den))
    except ValueError, ZeroDivisionError:
        return None
    return result if result > 0 else None


def _sar(value: object) -> Fraction:
    if isinstance(value, str) and ":" in value:
        num, _, den = value.partition(":")
        try:
            result = Fraction(int(num), int(den))
        except ValueError, ZeroDivisionError:
            return Fraction(1)
        return result if result > 0 else Fraction(1)
    return Fraction(1)


def ffprobe_json(path: Path) -> dict[str, Any]:
    """ffprobe's JSON (format and streams); MediaError when the file cannot be read."""
    binary = shutil.which("ffprobe")
    if binary is None:
        raise MediaError("ffprobe introuvable", hint="Installer FFmpeg (ffprobe dans le PATH).")
    cmd = [binary, "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    try:
        done = subprocess.run(
            [*cmd, str(path)], capture_output=True, text=True, timeout=_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffprobe trop long sur {path.name}") from exc
    if done.returncode != 0:
        detail = done.stderr.strip().splitlines()[-1:] or ["erreur inconnue"]
        raise MediaError(f"fichier illisible : {path.name} ({detail[0]})")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"réponse ffprobe invalide pour {path.name}") from exc
    if not isinstance(data, dict):
        raise MediaError(f"réponse ffprobe invalide pour {path.name}")
    return data


def _rotation(video: dict[str, Any]) -> int:
    """Display rotation in degrees, from the display matrix or the legacy `rotate` tag."""
    for side in video.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            try:
                return round(float(side["rotation"]))
            except TypeError, ValueError:
                return 0
    try:
        return round(float((video.get("tags") or {}).get("rotate", 0)))
    except TypeError, ValueError:
        return 0


def _ms(seconds: object) -> int | None:
    try:
        return round(float(str(seconds)) * 1000)
    except ValueError:
        return None


def parse_probe(data: dict[str, Any], name: str) -> ProbeInfo:
    """ProbeInfo from ffprobe's JSON. A +-90 degree rotation swaps width, height, display_width."""
    streams: list[dict[str, Any]] = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"aucune piste vidéo : {name}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise MediaError(f"dimensions illisibles : {name}")
    display_width = round(width * _sar(video.get("sample_aspect_ratio")))
    if abs(_rotation(video)) % 180 == 90:
        width, height, display_width = height, width, height

    average = _fraction(video.get("avg_frame_rate"))
    nominal = _fraction(video.get("r_frame_rate"))
    fps = average or nominal or Fraction(0)
    vfr = bool(
        average and nominal and abs(float(average - nominal)) / float(nominal) > _VFR_TOLERANCE
    )

    duration_ms = _ms((data.get("format") or {}).get("duration"))
    video_ms = _ms(video.get("duration"))
    if duration_ms is None:
        duration_ms = video_ms
    if duration_ms is None:
        raise MediaError(f"durée illisible : {name}")
    return ProbeInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        display_width=display_width,
        fps=fps,
        vfr=vfr,
        has_audio=audio is not None,
        vcodec=str(video.get("codec_name") or ""),
        acodec=str(audio.get("codec_name")) if audio is not None else None,
        video_duration_ms=video_ms if video_ms is not None else duration_ms,
    )


def probe(path: Path) -> ProbeInfo:
    """Facts about a video file; MediaError when it cannot be read as a video."""
    return parse_probe(ffprobe_json(path), path.name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_BLOCK):
            digest.update(block)
    return digest.hexdigest()
