"""Border logic of the cartoon cleaning (R45): only what touches an edge is ever removed."""

from collections.abc import Sequence

from rushes.core.intervals import Span, merge


def border_spans(
    spans: Sequence[Span], duration_ms: int, *, min_ms: int = 2000, eps_ms: int = 100
) -> list[Span]:
    """Spans longer than `min_ms` (as detected) that touch the start or end, snapped onto it."""
    found: list[Span] = []
    for start, end in spans:
        if end - start <= min_ms:  # the raw length counts, not the one after snapping
            continue
        touches_start, touches_end = start <= eps_ms, end >= duration_ms - eps_ms
        if touches_start or touches_end:
            found.append((0 if touches_start else start, duration_ms if touches_end else end))
    return found


def kept_range(duration_ms: int, removed: Sequence[Span]) -> Span:
    """What lies between the zone chained to the start and the zone chained to the end.

    A zone that touches neither edge nor a zone chained to one is ignored (nothing inside goes).
    """
    start, end = 0, duration_ms
    for zone_start, zone_end in merge(removed):
        if zone_start <= start:
            start = max(start, zone_end)
    for zone_start, zone_end in reversed(merge(removed)):
        if zone_end >= end:
            end = min(end, zone_start)
    if start >= end:
        raise ValueError("plus rien à garder après le nettoyage")
    return (start, end)
