"""Display identifiers and file names: e_000045 (episode), s_000007 (segment), p_000123 (post),
v_000012 (source video)."""

import re
from typing import Literal

Kind = Literal["e", "s", "p", "v"]

_KEY = re.compile(r"([espv])_(\d{6,})")


def _key(kind: Kind, value: int) -> str:
    if value < 0:
        raise ValueError(f"identifiant négatif : {value}")
    return f"{kind}_{value:06d}"


def episode_key(episode_id: int) -> str:
    return _key("e", episode_id)


def segment_key(segment_id: int) -> str:
    return _key("s", segment_id)


def post_key(post_id: int) -> str:
    return _key("p", post_id)


def source_key(source_video_id: int) -> str:
    return _key("v", source_video_id)


def parse_key(key: str) -> tuple[Kind, int]:
    match = _KEY.fullmatch(key)
    if not match:
        raise ValueError(
            f"identifiant invalide : {key!r} (attendu e_000045, s_000007, p_000123 ou v_000012)"
        )
    kinds: dict[str, Kind] = {"e": "e", "s": "s", "p": "p", "v": "v"}
    return kinds[match.group(1)], int(match.group(2))
