import pytest

from rushes.core.intervals import inside, merge, overlap_ms, subtract


def test_overlap_ms_partial_disjoint_and_touching() -> None:
    assert overlap_ms((0, 100), (50, 200)) == 50
    assert overlap_ms((50, 200), (0, 100)) == 50
    assert overlap_ms((0, 100), (100, 200)) == 0  # touching spans do not overlap
    assert overlap_ms((0, 100), (300, 400)) == 0
    assert overlap_ms((0, 1000), (200, 300)) == 100  # containment


def test_merge_sorts_and_joins_overlapping_and_touching() -> None:
    assert merge([(500, 600), (0, 100), (50, 120), (120, 130)]) == [(0, 130), (500, 600)]
    assert merge([]) == []


def test_merge_gap_is_inclusive() -> None:
    spans = [(0, 100), (150, 200)]
    assert merge(spans, gap_ms=49) == spans
    assert merge(spans, gap_ms=50) == [(0, 200)]


def test_merge_rejects_empty_inverted_spans_and_negative_gap() -> None:
    # a span must have start < end: silently dropping would hide a data bug
    with pytest.raises(ValueError):
        merge([(10, 10)])
    with pytest.raises(ValueError):
        merge([(20, 10)])
    with pytest.raises(ValueError):
        merge([(0, 10)], gap_ms=-1)


def test_subtract_cuts_holes_and_keeps_pieces() -> None:
    assert subtract((0, 1000), [(200, 300), (600, 700)]) == [(0, 200), (300, 600), (700, 1000)]
    assert subtract((0, 1000), [(0, 100)]) == [(100, 1000)]
    assert subtract((0, 1000), [(900, 1000)]) == [(0, 900)]
    assert subtract((0, 1000), [(0, 1000)]) == []
    assert subtract((0, 1000), []) == [(0, 1000)]


def test_subtract_handles_unsorted_overlapping_and_outside_holes() -> None:
    assert subtract((100, 500), [(400, 450), (-50, 150), (140, 200), (900, 1000)]) == [
        (200, 400),
        (450, 500),
    ]


def test_inside_bounds_are_inclusive() -> None:
    spans = [(100, 200), (300, 400)]
    assert inside(100, spans)
    assert inside(200, spans)
    assert inside(350, spans)
    assert not inside(99, spans)
    assert not inside(201, spans)
    assert not inside(250, spans)
    assert not inside(0, [])
