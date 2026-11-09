"""Seconds, milliseconds and "HH:MM:SS.mmm" timecodes."""

import math
import re
from datetime import UTC, datetime, time
from decimal import ROUND_FLOOR, Decimal

_TC = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})")
_HALF = Decimal("0.5")
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")


def s_to_ms(s: float) -> int:
    """Nearest millisecond, .5 rounds up (toward +infinity); exact on the decimal repr."""
    if not math.isfinite(s):
        raise ValueError(f"durée non finie : {s}")
    return int((Decimal(str(s)) * 1000 + _HALF).to_integral_value(ROUND_FLOOR))


def ms_to_s(ms: int) -> float:
    return ms / 1000


def ms_to_tc(ms: int) -> str:
    if ms < 0:
        raise ValueError(f"durée négative : {ms}")
    seconds, millis = divmod(ms, 1000)
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def tc_to_ms(tc: str) -> int:
    match = _TC.fullmatch(tc)
    if not match:
        raise ValueError(f"timecode invalide : {tc!r} (attendu 00:01:02.345)")
    hours, mins, secs, millis = (int(g) for g in match.groups())
    return ((hours * 60 + mins) * 60 + secs) * 1000 + millis


def parse_clock(value: str) -> time:
    """ "HH:MM" (00:00 to 23:59) as a time of day; ValueError in French otherwise."""
    found = _CLOCK.fullmatch(value) if isinstance(value, str) else None
    if found is None:
        raise ValueError(f"heure invalide : {value!r} (attendu HH:MM, de 00:00 à 23:59)")
    return time(int(found.group(1)), int(found.group(2)))


def parse_instant(value: str) -> datetime:
    """An ISO 8601 text as a UTC instant (text without an offset is UTC); ValueError in French."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"date invalide : {value!r} (attendu ISO 8601)") from exc
    return (moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)).astimezone(UTC)
