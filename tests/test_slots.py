"""`plan_slots` (task 8b): suggested publication times in absolute local days (R16)."""

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from math import floor
from zoneinfo import ZoneInfo

import pytest

from rushes.queue.slots import plan_slots

NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")
START = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # 07:00 in New York, Monday


def _local(moments: list[datetime], tz: ZoneInfo = NY) -> list[tuple[date, str]]:
    return [(m.astimezone(tz).date(), m.astimezone(tz).strftime("%H:%M")) for m in moments]


def _plan(**kwargs: object) -> list[datetime]:
    options: dict[str, object] = {
        "slots": ["15:00", "18:00"],
        "posts_per_day": 1,
        "tz": NY,
        "start": START,
        "taken": [],
        "n": 4,
    }
    options.update(kwargs)
    return plan_slots(**options)  # type: ignore[arg-type]


def test_plan_slots_one_per_day_tiktok() -> None:
    got = _local(_plan(n=4))
    days = [date(2026, 11, 17) + timedelta(days=i) for i in range(4)]
    assert [d for d, _ in got] == days  # from the day after `start`, one per local day
    assert [h for _, h in got] == [("15:00", "18:00")[d.toordinal() % 2] for d in days]


def test_plan_slots_returns_utc_and_converts_from_the_brand_zone() -> None:
    first = _plan(n=1)[0]
    assert first.tzinfo is not None and first.utcoffset() == timedelta(0)
    assert first.hour in (20, 23)  # 15:00 or 18:00 in New York in November (UTC-5)


def test_plan_slots_instagram_0_6_per_day() -> None:
    got = _plan(slots=["17:00"], posts_per_day=0.6, n=6)
    first_day = date(2026, 11, 17)
    assert len(got) == 6
    inside = [m for m in got if m.astimezone(NY).date() < first_day + timedelta(days=10)]
    assert len(inside) == 6  # 6 posts within 10 local days
    per_day = [
        sum(m.astimezone(NY).date() == first_day + timedelta(days=i) for m in got)
        for i in range(10)
    ]
    assert sorted(per_day, reverse=True)[:6] == [1] * 6 and sum(per_day) == 6
    # more than 10 days: exactly floor((d+1) * 0.6) - floor(d * 0.6) a day, whatever the start
    many = _plan(slots=["17:00"], posts_per_day=0.6, n=60)
    last = many[-1].astimezone(NY).date()
    days = (last - first_day).days + 1
    assert abs(len(many) - 0.6 * days) <= 1


def test_plan_slots_quota_is_exact_for_a_decimal_ratio() -> None:
    # floats give 0.7 x 10 = 7.000000000000001 and 0.1 x 3 = 0.30000000000000004: exact maths,
    # so that every single local day has exactly floor((d+1) x ppd) - floor(d x ppd) slots
    for text, days in (("0.1", 200), ("0.7", 100), ("0.6", 100), ("1.5", 100), ("0.3", 100)):
        ratio = Fraction(text)
        got = _plan(slots=["17:00", "19:00"], posts_per_day=float(text), n=10_000)
        first = date(2026, 11, 17).toordinal()
        per_day: dict[int, int] = {}
        for local_day, _ in _local(got):
            per_day[local_day.toordinal()] = per_day.get(local_day.toordinal(), 0) + 1
        for ordinal in range(first, first + days):
            quota = floor((ordinal + 1) * ratio) - floor(ordinal * ratio)
            assert per_day.get(ordinal, 0) == quota, (text, ordinal)


def test_plan_slots_stable_across_runs_24h_apart() -> None:
    first = _plan(posts_per_day=0.6, slots=["15:00", "18:00"], n=5)
    upcoming = [m for m in first if m > START + timedelta(hours=24)]
    second = _plan(
        posts_per_day=0.6,
        slots=["15:00", "18:00"],
        start=START + timedelta(hours=24),
        taken=upcoming,
        n=5,
    )
    # the days the two runs share keep the hour of the first run, none gets more than its quota
    merged = sorted({*first, *second})
    by_day: dict[date, list[datetime]] = {}
    for moment in merged:
        by_day.setdefault(moment.astimezone(NY).date(), []).append(moment)
    assert all(len(v) <= 1 for v in by_day.values())  # ceil(0.6) == 1
    alone = _plan(posts_per_day=0.6, slots=["15:00", "18:00"], n=12)
    assert set(merged) <= set(alone)  # every slot is one an unbroken run would have chosen


def test_plan_slots_stable_when_taken_has_the_day_already() -> None:
    day = _plan(posts_per_day=2, slots=["15:00", "18:00"], n=2)
    assert len(day) == 2
    rest = _plan(posts_per_day=2, slots=["15:00", "18:00"], taken=[day[0]], n=2)
    assert day[0] not in rest and rest[0] == day[1]  # the free hour of the day is the one left


def test_plan_slots_taken_is_compared_by_local_day_not_by_hour() -> None:
    first = _plan(n=1)[0]
    moved = first + timedelta(hours=1)  # Louis moved it by an hour: still that local day
    got = _plan(taken=[moved], n=1)[0]
    assert got.astimezone(NY).date() == first.astimezone(NY).date() + timedelta(days=1)


def test_plan_slots_taken_outside_the_window_is_ignored() -> None:
    old = START - timedelta(days=5)
    assert _plan(taken=[old, START], n=2) == _plan(n=2)


def test_plan_slots_full_day_is_skipped_and_empty_inputs() -> None:
    assert _plan(posts_per_day=0, n=3) == []
    assert _plan(n=0) == []
    with pytest.raises(ValueError, match="créneau"):
        _plan(slots=[])
    with pytest.raises(ValueError, match="HH:MM"):
        _plan(slots=["25:99"])


def test_plan_slots_dst_spring_forward_keeps_local_hour() -> None:
    start = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)  # New York jumps on Sunday 8 March
    got = _plan(slots=["15:00"], posts_per_day=1, start=start, n=5)
    assert {h for _, h in _local(got)} == {"15:00"}
    assert [m.hour for m in got] == [20, 19, 19, 19, 19]  # UTC-5 on the 7th, UTC-4 from the 8th


def test_plan_slots_dst_fall_back_and_nonexistent_hour() -> None:
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # fall back on 1 November
    got = _plan(slots=["15:00"], posts_per_day=1, start=start, n=4)
    assert [m.hour for m in got] == [19, 20, 20, 20]
    # 02:30 does not exist on 8 March in New York: the instant stays well defined and unique
    gap = _plan(slots=["02:30"], start=datetime(2026, 3, 7, 12, 0, tzinfo=UTC), n=1)[0]
    assert gap == _plan(slots=["02:30"], start=datetime(2026, 3, 7, 12, 0, tzinfo=UTC), n=1)[0]


def test_plan_slots_day_boundary_follows_the_brand_zone_not_utc() -> None:
    # 23:30 UTC on the 16th is already the 17th in Paris: the next local day is the 18th
    start = datetime(2026, 11, 16, 23, 30, tzinfo=UTC)
    paris = _plan(slots=["18:00"], tz=PARIS, start=start, n=1)[0]
    new_york = _plan(slots=["18:00"], tz=NY, start=start, n=1)[0]
    assert paris.astimezone(PARIS).date() == date(2026, 11, 18)
    assert new_york.astimezone(NY).date() == date(2026, 11, 17)


def test_plan_slots_does_not_depend_on_start_within_a_local_day() -> None:
    morning = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
    evening = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)  # still the 16th in New York
    assert _plan(start=morning, n=5) == _plan(start=evening, n=5)


def test_plan_slots_rejects_a_naive_start() -> None:
    with pytest.raises(ValueError, match="fuseau"):
        _plan(start=datetime(2026, 11, 16, 12, 0))


def test_plan_slots_a_quota_above_the_slot_count_repeats_an_hour() -> None:
    # the brief's formula: two posts a day on one slot give the same hour twice (documented)
    got = _plan(slots=["15:00"], posts_per_day=2, n=4)
    assert got[0] == got[1] and got[2] == got[3] and got[0] != got[2]


def test_plan_slots_a_naive_taken_is_a_french_value_error() -> None:
    with pytest.raises(ValueError, match="taken"):
        _plan(taken=[datetime(2026, 11, 17, 20, 0)])


@pytest.mark.parametrize("bad", ["15", "15:00:30", "9:00", "24:00", "12:60", ""])
def test_plan_slots_refuses_a_slot_that_is_not_hh_mm(bad: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        _plan(slots=["15:00", bad])


@pytest.mark.parametrize("bad", ["1\u0665:00", "15:\u0660\u0660", "\u0661\u0665:00"])
def test_plan_slots_refuses_non_ascii_digits(bad: str) -> None:
    # Arabic-Indic digits: `\d` takes them, a time of day does not
    with pytest.raises(ValueError, match="HH:MM"):
        _plan(slots=[bad])
