import pytest

from rushes.core.timecode import ms_to_s, ms_to_tc, s_to_ms, tc_to_ms


def test_s_to_ms_rounds_half_up() -> None:
    assert s_to_ms(1.0005) == 1001  # 1.0005 * 1000 is 1000.4999... in binary floats
    assert s_to_ms(1.0004) == 1000
    assert s_to_ms(0.0015) == 2
    assert s_to_ms(65) == 65000
    assert s_to_ms(0) == 0


def test_s_to_ms_negative_is_deterministic_half_up() -> None:
    # half up = toward +infinity: -0.0005 s -> 0, -1.0005 s -> -1000
    assert s_to_ms(-0.0005) == 0
    assert s_to_ms(-1.0005) == -1000
    assert s_to_ms(-1.0006) == -1001


def test_s_to_ms_rejects_non_finite() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            s_to_ms(bad)


def test_ms_to_s() -> None:
    assert ms_to_s(1500) == 1.5
    assert ms_to_s(0) == 0.0


def test_ms_to_tc_format() -> None:
    assert ms_to_tc(0) == "00:00:00.000"
    assert ms_to_tc(62345) == "00:01:02.345"
    assert ms_to_tc(3_600_000) == "01:00:00.000"
    assert ms_to_tc(100 * 3_600_000 + 1) == "100:00:00.001"


def test_ms_to_tc_rejects_negative() -> None:
    with pytest.raises(ValueError):
        ms_to_tc(-1)


def test_ms_tc_round_trip() -> None:
    for ms in (0, 1, 999, 1000, 62345, 3_599_999, 3_600_000, 86_399_999, 400_000_000):
        assert tc_to_ms(ms_to_tc(ms)) == ms


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1:02.345",
        "00:01:02",
        "00:01:02.3",
        "00:01:02.3456",
        "00:60:00.000",
        "00:00:60.000",
        "0:00:00.000",
        "-00:00:01.000",
        " 00:00:01.000",
        "00:00:01.000 ",
        "aa:bb:cc.ddd",
        "00:00:01,000",
        "00:00:01.000\n",
    ],
)
def test_tc_to_ms_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        tc_to_ms(bad)


def test_parse_instant_is_utc_and_french_on_garbage() -> None:
    from datetime import UTC, datetime

    from rushes.core.timecode import parse_instant

    expected = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    assert parse_instant("2026-11-17T20:00:00+00:00") == expected
    assert parse_instant("2026-11-17T20:00") == expected  # no offset: UTC
    assert parse_instant("2026-11-17T22:00:00+02:00") == expected
    assert parse_instant("2026-11-17T22:00:00+02:00").utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    for bad in ("", "demain", "2026-13-40"):
        with pytest.raises(ValueError, match="date invalide"):
            parse_instant(bad)
