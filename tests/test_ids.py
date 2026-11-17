import pytest

from rushes.ids import episode_key, parse_key, post_key, segment_key, source_key


def test_keys_are_zero_padded() -> None:
    assert episode_key(45) == "e_000045"
    assert segment_key(7) == "s_000007"
    assert post_key(123) == "p_000123"
    assert source_key(12) == "v_000012"
    assert episode_key(1_234_567) == "e_1234567"


def test_parse_key_round_trips() -> None:
    assert parse_key("e_000045") == ("e", 45)
    assert parse_key(segment_key(7)) == ("s", 7)
    assert parse_key(post_key(123)) == ("p", 123)
    assert parse_key(source_key(12)) == ("v", 12)


@pytest.mark.parametrize("bad", ["", "x_000001", "e_1", "e000001", "e_00000a", "E_000001"])
def test_parse_key_refuses_malformed_keys(bad: str) -> None:
    with pytest.raises(ValueError, match="identifiant"):
        parse_key(bad)


def test_negative_id_is_refused() -> None:
    with pytest.raises(ValueError, match="identifiant"):
        episode_key(-1)
