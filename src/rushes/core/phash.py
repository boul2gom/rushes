"""Perceptual-hash distances: 64-bit hashes written as 16 hex characters."""

import re
from collections.abc import Sequence

_HASH = re.compile(r"[0-9a-fA-F]{16}")


def is_phash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _value(h: str) -> int:
    if not _HASH.fullmatch(h):
        raise ValueError(f"pHash invalide : {h!r} (attendu 16 caractères hexadécimaux)")
    return int(h, 16)


def phash_distance(a: str, b: str) -> int:
    """Hamming distance in bits (0 to 64)."""
    return (_value(a) ^ _value(b)).bit_count()


def min_phash_distance(a: Sequence[str], b: Sequence[str]) -> int | None:
    """Smallest distance over all pairs; None when either side has no hash."""
    if not a or not b:
        return None
    return min(phash_distance(x, y) for x in a for y in b)
