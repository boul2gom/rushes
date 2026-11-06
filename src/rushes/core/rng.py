"""Seeded, reproducible draws: the same inputs always give the same result."""

import hashlib
import math
import random
from collections.abc import Mapping

from rushes.core.timecode import s_to_ms

_MASK_63 = (1 << 63) - 1
_SEP = "\x1f"


def derive_seed(*parts: str | int) -> int:
    """63-bit seed: first 8 bytes (big-endian) of sha256 of the parts joined by \\x1f.

    Parts are str()-ed: "1" and 1 collide, so callers keep a fixed positional schema.
    A str part holding the separator would collide with a split part: ValueError.
    """
    if any(isinstance(p, str) and _SEP in p for p in parts):
        raise ValueError("une partie de la graine contient le séparateur \\x1f")
    digest = hashlib.sha256(_SEP.join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") & _MASK_63


def draw_target_ms(seed: int, min_s: float, max_s: float) -> int:
    min_ms, max_ms = s_to_ms(min_s), s_to_ms(max_s)
    if min_ms > max_ms:
        raise ValueError(f"bornes inversées : {min_s} > {max_s}")
    return random.Random(seed).randint(min_ms, max_ms)


def weighted_choice[T: str](seed: int, weights: Mapping[T, float]) -> T:
    """Pick a key with probability proportional to its weight; zero weights are ignored."""
    if any(not math.isfinite(w) or w < 0 for w in weights.values()):
        raise ValueError("poids négatif ou non fini")
    pool = [(key, weights[key]) for key in sorted(weights) if weights[key] > 0]
    if not pool:
        raise ValueError("aucun poids positif")
    point = random.Random(seed).random() * sum(w for _, w in pool)
    cumulative = 0.0
    for key, weight in pool:
        cumulative += weight
        if point < cumulative:
            return key
    return pool[-1][0]  # float rounding at the upper edge
