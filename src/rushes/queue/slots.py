"""Suggested publication times (task 8b), shared with the queue of task 16b.

Local days of the brand are numbered in absolute terms (`date.toordinal()`), never from `start`,
which moves at every run: a day then gets the same quota and the same hours whatever the run that
looks at it (R16). Light imports only: `rushes serve` reads this module (task 36).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from rushes.core.timecode import parse_clock

MAX_DAYS = 20_000  # a ratio so small that n slots do not fit in 55 years is a configuration slip


def _parse(slots: Sequence[str]) -> list[time]:
    if not slots:
        raise ValueError("aucun créneau horaire configuré")
    return [parse_clock(raw) for raw in slots]


def _quota(day: int, ratio: Fraction) -> int:
    """floor((d + 1) x ppd) - floor(d x ppd): exact, the sum over any days is floor-balanced."""
    return int((day + 1) * ratio // 1 - day * ratio // 1)


def _moment(day: date, hour: time, tz: ZoneInfo) -> datetime:
    """The local hour of the day as a UTC instant (an hour skipped by DST resolves forward)."""
    return datetime.combine(day, hour, tzinfo=tz).astimezone(UTC)


def _nearest(slots: list[datetime], moment: datetime) -> datetime:
    """The slot at the same instant, else the closest one (the earlier on a tie)."""
    return min(slots, key=lambda slot: (abs(slot - moment), slot))


def _free_hours(
    day: date, hours: list[time], tz: ZoneInfo, claimed: Sequence[datetime]
) -> list[datetime]:
    """The day's hours minus the taken ones: the same hour first, else the nearest in time."""
    left = sorted(_moment(day, hour, tz) for hour in hours)
    for moment in sorted(claimed):
        if not left:
            break
        left.remove(_nearest(left, moment))
    return left


def plan_slots(
    *,
    slots: Sequence[str],
    posts_per_day: float,
    tz: ZoneInfo,
    start: datetime,
    taken: Sequence[datetime],
    n: int,
) -> list[datetime]:
    """The next `n` free slots (UTC, ascending), from the local day after `start`.

    Day `d` (`toordinal()` of the local date) has `floor((d+1) x ppd) - floor(d x ppd)` slots, at
    the hours `slots[(d + j) % len(slots)]` for `j` below that quota. `taken` takes from a day's
    quota as many slots as it holds that local day (compared by local day, not by exact hour).
    A quota above the number of slots repeats an hour of the day (the formula is the brief's).
    `start` and `taken` must carry a time zone (ValueError in French otherwise).
    """
    if start.tzinfo is None:
        raise ValueError("start doit porter un fuseau horaire")
    if any(moment.tzinfo is None for moment in taken):
        raise ValueError("taken doit porter un fuseau horaire")
    hours = _parse(slots)
    ratio = Fraction(str(posts_per_day))
    if n <= 0 or ratio <= 0:
        return []
    claimed: dict[date, list[datetime]] = {}
    for moment in taken:
        claimed.setdefault(moment.astimezone(tz).date(), []).append(moment)
    day = start.astimezone(tz).date() + timedelta(days=1)
    found: list[datetime] = []
    for _ in range(MAX_DAYS):
        ordinal = day.toordinal()
        quota = _quota(ordinal, ratio)
        if quota:
            chosen = [hours[(ordinal + j) % len(hours)] for j in range(quota)]
            found += _free_hours(day, chosen, tz, claimed.get(day, []))
            if len(found) >= n:
                return found[:n]
        day += timedelta(days=1)
    return found
