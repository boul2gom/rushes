import pytest

from rushes.core.phash import is_phash, min_phash_distance, phash_distance


def test_phash_distance_extremes_and_single_bit() -> None:
    assert phash_distance("0" * 16, "f" * 16) == 64
    assert phash_distance("0" * 16, "0" * 16) == 0
    assert phash_distance("0" * 16, "0" * 15 + "1") == 1
    assert phash_distance("ABCDEF0123456789", "abcdef0123456789") == 0  # case-insensitive


def test_phash_distance_rejects_malformed() -> None:
    for bad in ("", "0" * 15, "0" * 17, "g" * 16):
        with pytest.raises(ValueError):
            phash_distance(bad, "0" * 16)


def test_min_phash_distance_takes_the_closest_pair() -> None:
    near = "0" * 15 + "3"  # 2 bits from zero
    assert min_phash_distance(["f" * 16, "0" * 16], [near]) == 2


def test_min_phash_distance_is_none_without_hashes() -> None:
    assert min_phash_distance([], ["0" * 16]) is None
    assert min_phash_distance(["0" * 16], []) is None
    assert min_phash_distance([], []) is None


def test_is_phash_accepts_only_16_hex_strings() -> None:
    assert is_phash("0" * 16)
    assert is_phash("ABCDEF0123456789")
    for bad in ("", "0" * 15, "0" * 17, "g" * 16, None, 1, b"0" * 16, ["0" * 16]):
        assert not is_phash(bad)
