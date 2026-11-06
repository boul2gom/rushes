"""Half-open time spans in integer milliseconds."""

from collections.abc import Iterable, Sequence

type Span = tuple[int, int]


def _check(span: Span) -> Span:
    # a span needs start < end: dropping empty or inverted ones silently would hide a data bug
    if span[0] >= span[1]:
        raise ValueError(f"intervalle vide ou inversé : {span}")
    return span


def overlap_ms(a: Span, b: Span) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def merge(spans: Iterable[Span], *, gap_ms: int = 0) -> list[Span]:
    """Sorted union; spans separated by at most `gap_ms` are joined."""
    if gap_ms < 0:
        raise ValueError(f"gap_ms négatif : {gap_ms}")
    merged: list[Span] = []
    for start, end in sorted(_check(s) for s in spans):
        if merged and start - merged[-1][1] <= gap_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract(span: Span, holes: Iterable[Span]) -> list[Span]:
    """The parts of `span` not covered by `holes`."""
    start, end = _check(span)
    pieces: list[Span] = []
    cursor = start
    for hole_start, hole_end in merge(holes):
        if hole_end <= cursor:
            continue
        if hole_start >= end:
            break
        if hole_start > cursor:
            pieces.append((cursor, hole_start))
        cursor = max(cursor, hole_end)
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def inside(t_ms: int, spans: Sequence[Span]) -> bool:
    """True when `t_ms` lies in one of the spans, bounds included."""
    return any(start <= t_ms <= end for start, end in spans)
