"""Delivery check (R7): what a rendered file must be, read back with ffprobe.

Also the one place of the delivery constants (size, frame rate, audio rate) that `render` uses.
"""

import struct
from pathlib import Path
from typing import Any

from rushes.errors import MediaError
from rushes.media.probe import ffprobe_json, parse_probe

WIDTH, HEIGHT = 1080, 1920
FRAME_RATE = 30  # constant, images per second
SAMPLE_RATE = 48_000
SILENT_TRACK = f"anullsrc=r={SAMPLE_RATE}:cl=stereo"  # lavfi source: silent stereo AAC input
# ftyp major brands of an MP4 (ISO base media, MP4 v1/v2, AVC, iTunes video); QuickTime is not one
MP4_BRANDS: tuple[str, ...] = (
    "isom",
    "iso2",
    "iso4",
    "iso5",
    "iso6",
    "mp41",
    "mp42",
    "avc1",
    "M4V",
)
_BRANDS_SHOWN = "isom/mp41/mp42"
_RATE = f"{FRAME_RATE}/1"
_HEADER = 8


def top_level_atoms(path: Path) -> list[tuple[str, bytes]]:
    """(type, first 8 payload bytes) of the file's top-level MP4 atoms, without reading the data.

    MediaError (French) on a truncated or impossible atom; never struct.error."""
    atoms: list[tuple[str, bytes]] = []
    total = path.stat().st_size
    with path.open("rb") as handle:
        position = 0
        while position < total:
            handle.seek(position)
            header = handle.read(_HEADER)
            if len(header) < _HEADER:
                raise MediaError(f"atome tronqué à l'octet {position} de {path.name}")
            size, raw_kind = struct.unpack(">I4s", header)
            head = _HEADER
            if size == 1:  # the real size follows the type, on 64 bits
                extended = handle.read(8)
                if len(extended) < 8:
                    raise MediaError(f"atome tronqué à l'octet {position} de {path.name}")
                (size,) = struct.unpack(">Q", extended)
                head += 8
            elif size == 0:  # runs to the end of the file
                size = total - position
            if size < head:
                raise MediaError(f"atome invalide à l'octet {position} de {path.name}")
            if position + size > total:
                raise MediaError(f"atome tronqué à l'octet {position} de {path.name}")
            handle.seek(position + head)
            atoms.append((raw_kind.decode("latin-1"), handle.read(8)))
            position += size
    return atoms


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'s' if number > 1 else ''}"  # French: zero takes the singular


def _video_problems(video: dict[str, Any], displayed: tuple[int, int]) -> list[str]:
    problems: list[str] = []
    if displayed != (WIDTH, HEIGHT):
        problems.append(
            f"dimensions affichées {displayed[0]}x{displayed[1]} au lieu de {WIDTH}x{HEIGHT}"
        )
    codec, profile = video.get("codec_name"), video.get("profile")
    if codec != "h264":
        problems.append(f"codec vidéo {codec} au lieu de h264")
    elif profile != "High":
        problems.append(f"profil H.264 {profile} au lieu de High")
    if video.get("pix_fmt") != "yuv420p":
        problems.append(f"format de pixel {video.get('pix_fmt')} au lieu de yuv420p (8 bits)")
    nominal, average = video.get("r_frame_rate"), video.get("avg_frame_rate")
    if nominal != _RATE or average != _RATE:
        wanted = f"{FRAME_RATE} i/s constantes ({_RATE})"
        problems.append(f"cadence {average} (nominale {nominal}) au lieu de {wanted}")
    return problems


def _audio_problems(audio: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if audio.get("codec_name") != "aac":
        problems.append(f"codec audio {audio.get('codec_name')} au lieu de aac")
    if str(audio.get("sample_rate")) != str(SAMPLE_RATE):
        problems.append(
            f"fréquence audio {audio.get('sample_rate')} Hz au lieu de {SAMPLE_RATE} Hz"
        )
    return problems


def _container_problems(path: Path, data: dict[str, Any]) -> list[str]:
    names = str((data.get("format") or {}).get("format_name", "")).split(",")
    problems: list[str] = []
    if "mp4" not in names:
        problems.append(f"conteneur {','.join(names)} au lieu de mp4")
    try:
        atoms = top_level_atoms(path)
    except MediaError as exc:
        return [*problems, str(exc)]
    kinds = [kind for kind, _ in atoms]
    brand = next(
        (payload[:4].decode("latin-1").strip() for kind, payload in atoms if kind == "ftyp"), ""
    )
    if brand not in MP4_BRANDS:
        found = brand or "absente"
        problems.append(f"marque ftyp {found} au lieu de {_BRANDS_SHOWN}")
    if "moov" not in kinds or "mdat" not in kinds or kinds.index("moov") > kinds.index("mdat"):
        problems.append("+faststart absent : l'atome moov doit précéder mdat")
    return problems


def check_delivery(path: Path) -> list[str]:
    """Violations of R7 as French messages, one per point; [] when the file conforms.

    The size is the displayed one (pixel aspect ratio and rotation applied). The loudness is not
    measured here (it costs a full decode): `render` reads it from the second loudnorm pass and
    the tests check it with ebur128.
    """
    try:
        data = ffprobe_json(path)
    except MediaError as exc:
        return [str(exc)]
    problems = _container_problems(path, data)
    streams: list[dict[str, Any]] = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(videos) != 1:
        problems.append(f"{_count(len(videos), 'piste vidéo')} au lieu d'une")
    else:
        try:
            info = parse_probe(data, path.name)
            problems += _video_problems(videos[0], (info.display_width, info.height))
        except MediaError as exc:
            problems.append(str(exc))
    if len(audios) != 1:
        problems.append(f"{_count(len(audios), 'piste audio')} au lieu d'une")
    else:
        problems += _audio_problems(audios[0])
    return problems
