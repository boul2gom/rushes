"""Perceptual-hash distances: 64-bit hashes written as 16 hex characters."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

_HASH = re.compile(r"[0-9a-fA-F]{16}")
_MIN_BITS = 5  # a usable hash has at least this many bits of each value


def is_phash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _value(h: str) -> int:
    if not _HASH.fullmatch(h):
        raise ValueError(f"pHash invalide : {h!r} (attendu 16 caractères hexadécimaux)")
    return int(h, 16)


def _degenerate(value: int) -> bool:
    """The one definition: fewer than 5 bits set, or fewer than 5 bits clear (decision C53)."""
    ones = value.bit_count()
    return ones < _MIN_BITS or 64 - ones < _MIN_BITS


def is_degenerate_phash(h: str) -> bool:
    """Black or flat image (see `_degenerate`)."""
    return _degenerate(_value(h))


def phash_distance(a: str, b: str) -> int:
    """Hamming distance in bits (0 to 64)."""
    return (_value(a) ^ _value(b)).bit_count()


def usable_phashes(hashes: Sequence[str]) -> list[int]:
    """Hash values of the shots that carry information; black or flat ones are dropped (C53)."""
    values = [_value(h) for h in hashes]
    return [v for v in values if not _degenerate(v)]


@dataclass(frozen=True)
class CloseShots:
    """How two sets of shots (usable hash values) resemble each other."""

    mine: int  # shots of the first set with a close shot in the second
    theirs: int  # shots of the second set with a close shot in the first
    distance: int | None  # smallest distance over all pairs; None when either set is empty


def close_shots(mine: Sequence[int], theirs: Sequence[int], max_distance: int) -> CloseShots:
    """Shots of each set with a shot within `max_distance` (inclusive) in the other."""
    mine_close = [False] * len(mine)
    theirs_close = [False] * len(theirs)
    best: int | None = None
    for i, a in enumerate(mine):
        for j, b in enumerate(theirs):
            d = (a ^ b).bit_count()
            if best is None or d < best:
                best = d
            if d <= max_distance:
                mine_close[i] = theirs_close[j] = True
    return CloseShots(sum(mine_close), sum(theirs_close), best)


MIN_HELD_SHOTS = 3  # a held passage this short is only compared from the candidate's side (C53)


@dataclass(frozen=True)
class PhashConflict:
    direction: Literal[
        "mine", "theirs"
    ]  # whose shots reach the share: the candidate's or the held one's
    close: int  # shots of that passage with a close shot in the other
    total: int  # usable shots of that passage
    distance: int  # smallest distance between the two passages


def phash_conflict(
    mine: Sequence[int], theirs: Sequence[int], *, max_distance: int, min_share: float
) -> PhashConflict | None:
    """R1 on two passages (C53). Inputs come from `usable_phashes`.

    Conflict when `min_share` of the candidate's shots have a close shot in the held passage, or
    (held passage of at least 3 shots) when `min_share` of its shots have one in the candidate.
    """
    if not mine or not theirs:
        return None
    share = Fraction(repr(min_share))  # exact: 0.25 of 4 shots is 1 shot
    found = close_shots(mine, theirs, max_distance)
    if found.distance is None:  # unreachable: both sets are non-empty
        return None
    if found.mine and Fraction(found.mine, len(mine)) >= share:
        return PhashConflict("mine", found.mine, len(mine), found.distance)
    if (
        len(theirs) >= MIN_HELD_SHOTS
        and found.theirs
        and Fraction(found.theirs, len(theirs)) >= share
    ):
        return PhashConflict("theirs", found.theirs, len(theirs), found.distance)
    return None
