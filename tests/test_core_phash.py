import pytest

from helpers import synthetic_phash
from rushes.core.phash import (
    CloseShots,
    PhashConflict,
    close_shots,
    is_degenerate_phash,
    is_phash,
    phash_conflict,
    phash_distance,
    usable_phashes,
)


def test_phash_distance_extremes_and_single_bit() -> None:
    assert phash_distance("0" * 16, "f" * 16) == 64
    assert phash_distance("0" * 16, "0" * 16) == 0
    assert phash_distance("0" * 16, "0" * 15 + "1") == 1
    assert phash_distance("ABCDEF0123456789", "abcdef0123456789") == 0  # case-insensitive


def test_is_phash_accepts_only_16_hex_strings() -> None:
    assert is_phash("0" * 16)
    assert is_phash("ABCDEF0123456789")
    for bad in ("", "0" * 15, "0" * 17, "g" * 16, None, 1, b"0" * 16, ["0" * 16]):
        assert not is_phash(bad)


def test_phash_distance_rejects_malformed() -> None:
    for bad in ("", "0" * 15, "0" * 17, "g" * 16):
        with pytest.raises(ValueError):
            phash_distance(bad, "0" * 16)


BIT_CASES = [
    ("0" * 16, True),  # 0 bits set
    ("000000000000000f", True),  # 4 bits set
    ("000000000000001f", False),  # 5 bits set
    ("ffffffffffffffe0", False),  # 59 bits set (5 zeros)
    ("ffffffffffffffff", True),  # 64 bits set
    ("fffffffffffffff0", True),  # 60 bits set (4 zeros)
    ("ABCDEF0123456789", False),
]


@pytest.mark.parametrize(("value", "degenerate"), BIT_CASES)
def test_r1_is_degenerate_phash_counts_bits_on_both_sides(value: str, degenerate: bool) -> None:
    assert is_degenerate_phash(value) is degenerate


def test_r1_is_degenerate_phash_rejects_malformed() -> None:
    for bad in ("", "0" * 15, "g" * 16):
        with pytest.raises(ValueError):
            is_degenerate_phash(bad)


@pytest.mark.parametrize(("value", "degenerate"), BIT_CASES)
def test_r1_usable_phashes_uses_the_same_bounds(value: str, degenerate: bool) -> None:
    assert usable_phashes([value]) == ([] if degenerate else [int(value, 16)])


def test_r1_usable_phashes_keeps_order_and_rejects_malformed() -> None:
    kept = usable_phashes(["0" * 16, "5" * 16, "f" * 16, "000000000000001f"])
    assert kept == [0x5555555555555555, 0x1F]
    with pytest.raises(ValueError):
        usable_phashes(["xyz"])


def test_r1_close_shots_counts_each_side_and_takes_the_smallest_distance() -> None:
    a = [0b0, 0xFFFF_FFFF, 0xFFFF_FFFF_0000_0000]
    b = [0b1111, 0xFFFF_FFFF_0000_0000 ^ 0b11]  # a[0]~b[0] at 4; a[2]~b[1] at 2
    found = close_shots(a, b, 4)
    assert found == CloseShots(mine=2, theirs=2, distance=2)
    assert close_shots(a, b, 3) == CloseShots(mine=1, theirs=1, distance=2)  # 4 > 3
    assert close_shots(a, [], 4) == CloseShots(0, 0, None)


def test_r1_close_shots_counts_are_not_symmetric() -> None:
    a, far = 0x5555_5555_5555_5555, 0xAAAA_AAAA_AAAA_AAAA
    one = [a ^ 0b1]  # close to two of the three held shots
    held = [a, a ^ 0b110, far]
    assert close_shots(one, held, 4) == CloseShots(mine=1, theirs=2, distance=1)
    assert close_shots(held, one, 4) == CloseShots(mine=2, theirs=1, distance=1)


S = 0x5555555555555555


def _far(n: int) -> list[int]:
    """n usable shots, more than 10 bits from each other (19 at least for n <= 12)."""
    return [int(f"{0x9E3779B97F4A7C15 * (i + 3) % 2**64:016x}", 16) for i in range(n)]


def test_r1_phash_conflict_both_directions_and_minimum_of_three_held_shots() -> None:
    fars = _far(12)
    assert all((x ^ y).bit_count() > 10 for i, x in enumerate(fars) for y in fars[i + 1 :])
    kw = {"max_distance": 10, "min_share": 0.25}
    held, others = fars[:4], fars[4:]  # unrelated to each other
    copy = held[0] ^ 1  # one shot of the held passage, found again in the candidate
    cand = [copy, *others]  # 1 of 9 on the candidate side: below the share
    # held side: 1 of 4 (25 %) and 1 of 3 refuse, 1 of 5 (20 %) accepts, 1 of 2 is not compared
    assert phash_conflict(cand, held, **kw) == PhashConflict("theirs", 1, 4, 1)
    assert phash_conflict(cand, [*held, S], **kw) is None
    assert phash_conflict(cand, held[:3], **kw) == PhashConflict("theirs", 1, 3, 1)
    assert phash_conflict(cand, held[:2], **kw) is None
    # candidate side: 1 of 4 refuses whatever the size of the held passage
    assert phash_conflict([copy, *others[:3]], held[:1], **kw) == PhashConflict("mine", 1, 4, 1)
    assert phash_conflict([], held, **kw) is None
    assert phash_conflict(cand, [], **kw) is None


def test_synthetic_test_hashes_are_usable_and_never_close_to_each_other() -> None:
    hashes = [synthetic_phash(i) for i in range(300)]
    assert not any(is_degenerate_phash(h) for h in hashes)
    assert all(phash_distance(a, b) > 10 for i, a in enumerate(hashes) for b in hashes[i + 1 :])


def test_r1_held_side_share_is_exact_in_decimal() -> None:
    fars = _far(200)
    held, others = fars[:100], fars[100:]  # unrelated to each other (18 bits at least)
    cand = [h ^ 1 for h in held[:7]] + others  # 7 of 100 held shots found; 7 of 107 on my side
    kw = {"max_distance": 10}
    assert phash_conflict(cand, held, min_share=0.07, **kw) == PhashConflict("theirs", 7, 100, 1)
    assert phash_conflict(cand, held, min_share=0.08, **kw) is None  # 7 % < 8 %
