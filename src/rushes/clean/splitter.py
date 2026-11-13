"""Splitting a compilation into episodes (task 11): where the cuts go, from facts found elsewhere.

Pure functions on milliseconds. The cuts come from the series theme when it plays inside the video;
without it, from the black pictures that separate two episodes. A black is enough: the music often
runs over it, so requiring a silence too misses real boundaries (measured on Louis's Oggy file).
A black that also overlaps a silence only wins a tie.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from rushes.config import CleanSettings
from rushes.core.intervals import Span

EDGE_MS = 2000  # a theme this close to the start is the video's own opening, not a cut
# Two blacks this close are one boundary: episodes end on a black, show a title card of about 6 s
# (the next episode's opening) and go black again before the picture starts (Oggy, Louis's files).
MERGE_GAP_MS = 8000
# A merged boundary never spans more than this: a run of blacks every few seconds (a flashing
# montage) must not become one huge zone that belongs to no episode and hides a real boundary.
MERGE_MAX_MS = 20_000


@dataclass(frozen=True)
class Boundary:
    """A black picture long enough to separate two episodes; it belongs to neither of them."""

    start: int
    end: int
    silent: bool  # a silence overlaps it too: preferred when two boundaries are equally good


def black_boundaries(
    blacks: Sequence[Span], silences: Sequence[Span], *, min_ms: int
) -> list[Boundary]:
    """The blacks of at least `min_ms`, in time order, each marked when a silence overlaps it.

    Blacks at most MERGE_GAP_MS apart are one boundary (black, title card, black): the card belongs
    to neither neighbour. A merged boundary spans at most MERGE_MAX_MS: a longer run of blacks
    starts a new one. "Overlaps" means at least `min_ms` in common.
    """
    kept = sorted(b for b in blacks if b[1] - b[0] >= min_ms)
    found: list[Boundary] = []
    for start, end in kept:
        silent = any(min(end, s_end) - max(start, s_start) >= min_ms for s_start, s_end in silences)
        last = found[-1] if found else None
        if (
            last is not None
            and start - last.end <= MERGE_GAP_MS
            and end - last.start <= MERGE_MAX_MS
        ):
            found[-1] = Boundary(last.start, max(last.end, end), last.silent or silent)
        else:
            found.append(Boundary(start, end, silent))
    return found


def _best(length_of: dict[Boundary, int], typical: int) -> Boundary:
    """Closest to the typical length; then a silent one; then the earliest."""
    return min(length_of, key=lambda b: (abs(length_of[b] - typical), not b.silent, b.start, b.end))


def _boundary_cuts(
    first: int,
    last: int,
    boundaries: Sequence[Boundary],
    s: CleanSettings,
    *,
    always: bool,
) -> list[Boundary]:
    """Boundaries that cut [first, last] into episodes, in order.

    From the start of the piece, take the boundary giving an episode of `episode_min_s` to
    `episode_max_s` that is closest to `typical_episode_s`, then go on from the end of that boundary
    (`always`: as long as one fits; else only while what remains is longer than the maximum). A
    remainder longer than the maximum that no such boundary cuts is cut at the fitting boundary
    (episode of at least the minimum, remainder of at least the minimum) closest to the typical
    length; if there is none, it stays whole.
    """
    low, high, typical = s.episode_min_s * 1000, s.episode_max_s * 1000, s.typical_episode_s * 1000
    inside = [b for b in boundaries if first < b.start and b.end < last]
    cuts: list[Boundary] = []
    previous = first
    while True:
        remaining = last - previous
        if remaining <= high and not always:
            return cuts
        usual = {b: b.start - previous for b in inside if low <= b.start - previous <= high}
        if not usual and remaining > high:  # the tail is too long and nothing usual cuts it
            usual = {
                b: b.start - previous
                for b in inside
                if b.start - previous >= low and last - b.end >= low
            }
        if not usual:
            return cuts
        pick = _best(usual, typical)
        cuts.append(pick)
        previous = pick.end


def split_spans(
    duration_ms: int,
    *,
    theme_starts: Sequence[int],
    boundaries: Sequence[Boundary],
    s: CleanSettings,
) -> list[Span]:
    """The episodes of a compilation as spans of the source; [] when it is not one (stays whole).

    A video is looked at when it lasts `compilation_min_s` or when the theme plays at least twice
    inside it (not at its very start). The cuts are the starts of those theme peaks, else the black
    boundaries; a piece cut by the theme that is longer than `episode_max_s` is cut further on the
    boundaries. A black boundary is in no episode: the previous one ends where it starts, the next
    one begins where it ends. Pieces under `episode_min_s` are dropped; fewer than two pieces left
    means nothing was separated.

    Two peaks closer than `episode_min_s` are a closing generic and the next opening (the same
    theme): the cut is at the later one, the opening. The closing generic stays at the end of the
    previous episode, where the closing rule removes it, and so does what lies between the two
    (a teaser): an accepted loss, the opening of the next episode must start its child.
    """
    inside = _theme_cuts(
        sorted({t for t in theme_starts if EDGE_MS < t < duration_ms - EDGE_MS}),
        s.episode_min_s * 1000,
    )
    if duration_ms < s.compilation_min_s * 1000 and len(inside) < 2:
        return []
    usable = [b for b in boundaries if 0 < b.start < duration_ms]
    pieces: list[Span]
    if inside:
        pieces = []
        for first, last in pairwise([0, *inside, duration_ms]):
            pieces += _gaps_out(first, last, _boundary_cuts(first, last, usable, s, always=False))
    else:
        pieces = _gaps_out(0, duration_ms, _boundary_cuts(0, duration_ms, usable, s, always=True))
    kept = [(a, b) for a, b in pieces if b - a >= s.episode_min_s * 1000]
    return kept if len(kept) >= 2 else []


def _theme_cuts(starts: Sequence[int], min_ms: int) -> list[int]:
    """The cuts of the theme peaks: of peaks closer than `min_ms` only the last one cuts."""
    cuts: list[int] = []
    for t in starts:
        if cuts and t - cuts[-1] < min_ms:
            cuts[-1] = t
        else:
            cuts.append(t)
    return cuts


def _gaps_out(first: int, last: int, cuts: Sequence[Boundary]) -> list[Span]:
    """The pieces of [first, last] between cuts, each cut's black left out of both neighbours."""
    starts = [first, *(c.end for c in cuts)]
    ends = [*(c.start for c in cuts), last]
    return list(zip(starts, ends, strict=True))
