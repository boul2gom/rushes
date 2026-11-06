import random

import pytest

from rushes.core.rng import derive_seed, draw_target_ms, weighted_choice


def test_derive_seed_is_stable() -> None:
    # frozen: sha256("a\x1fb\x1f1"), first 8 bytes big-endian, masked to 63 bits
    assert derive_seed("a", "b", 1) == 4178445970226852233
    assert derive_seed("x") == 3274422879871479876


def test_derive_seed_depends_on_parts_and_fits_63_bits() -> None:
    assert derive_seed("a", "b") != derive_seed("b", "a")
    assert derive_seed("ab", "c") != derive_seed("a", "bc")  # the separator matters
    assert all(0 <= derive_seed("s", n) < 2**63 for n in range(200))


def test_derive_seed_rejects_the_separator_inside_a_part() -> None:
    with pytest.raises(ValueError):
        derive_seed("a\x1fb")
    with pytest.raises(ValueError):
        derive_seed("ok", "x\x1f")


def test_draw_target_ms_stays_in_range_over_1000_seeds() -> None:
    draws = [draw_target_ms(seed, 65, 100) for seed in range(1000)]
    assert all(65000 <= d <= 100000 for d in draws)
    assert len(set(draws)) > 100  # not constant


def test_draw_target_ms_is_deterministic_and_matches_randint() -> None:
    assert draw_target_ms(42, 65, 100) == draw_target_ms(42, 65, 100)
    assert draw_target_ms(42, 65, 100) == random.Random(42).randint(65000, 100000)


def test_draw_target_ms_degenerate_and_inverted_range() -> None:
    assert draw_target_ms(7, 90, 90) == 90000
    with pytest.raises(ValueError):
        draw_target_ms(7, 100, 65)


def test_weighted_choice_is_deterministic_and_order_independent() -> None:
    forward = {"a": 1.0, "b": 2.0, "c": 3.0}
    backward = {"c": 3.0, "b": 2.0, "a": 1.0}
    for seed in range(50):
        assert weighted_choice(seed, forward) == weighted_choice(seed, backward)
    assert {weighted_choice(seed, forward) for seed in range(200)} == {"a", "b", "c"}


def test_weighted_choice_ignores_zero_weights() -> None:
    weights = {"a": 0.0, "b": 1.0, "c": 0.0}
    assert {weighted_choice(seed, weights) for seed in range(100)} == {"b"}


def test_weighted_choice_follows_weights_roughly() -> None:
    picks = [weighted_choice(seed, {"a": 1.0, "b": 9.0}) for seed in range(2000)]
    assert 1500 < picks.count("b") < 1950


def test_weighted_choice_rejects_no_positive_weight_and_bad_weights() -> None:
    with pytest.raises(ValueError):
        weighted_choice(1, {})
    with pytest.raises(ValueError):
        weighted_choice(1, {"a": 0.0, "b": 0})
    with pytest.raises(ValueError):
        weighted_choice(1, {"a": -1.0, "b": 2.0})
    with pytest.raises(ValueError):
        weighted_choice(1, {"a": float("nan")})
