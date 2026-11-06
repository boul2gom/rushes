"""Seconds, milliseconds and "HH:MM:SS.mmm" timecodes."""

import math
import re
from decimal import ROUND_FLOOR, Decimal

_TC = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})")
_HALF = Decimal("0.5")


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
