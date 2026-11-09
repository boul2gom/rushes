"""Derived values (master 5.7): config value > derived value > default.

`derived_values` holds the current value of a key in a scope; every `record` also adds a line to
`derived_values_history`, so a recalibration can be audited. Nothing here commits: the caller's
transaction does.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from rushes.db.models import DerivedValue, DerivedValueHistory


@dataclass(frozen=True)
class Resolved[T]:
    value: T
    origin: Literal["config", "derived", "default"]
    source: str | None
    computed_at: datetime | None


def record(
    session: Session,
    key: str,
    scope: str,
    value: object,
    *,
    source: str,
    confidence: float,
    at: datetime,
) -> None:
    """Set the current value of `key` in `scope` and add a history line."""
    if at.tzinfo is None:
        raise ValueError("`at` doit être une date avec fuseau horaire")
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError(f"confiance hors de [0, 1] : {confidence}")
    try:
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"valeur non sérialisable en JSON : {exc}") from exc
    computed_at = at.astimezone(UTC).isoformat(timespec="seconds")
    fields = {
        "value_json": value_json,
        "source": source,
        "confidence": confidence,
        "computed_at": computed_at,
    }
    current = session.get(DerivedValue, (key, scope))
    if current is None:
        session.add(DerivedValue(key=key, scope=scope, **fields))
    else:
        for name, field_value in fields.items():
            setattr(current, name, field_value)
    session.add(DerivedValueHistory(key=key, scope=scope, **fields))
    session.flush()


def resolve[T](
    session: Session,
    key: str,
    scope: str,
    *,
    config_value: T | None,
    default: T,
    parse: Callable[[object], T],
) -> Resolved[T]:
    """The value to use: the config's if given, else the derived one, else `default`.

    A derived value that `parse` refuses (ValueError, TypeError, KeyError) is ignored: a bad
    recalibration must not stop the pipeline, the default applies."""
    if config_value is not None:
        return Resolved(config_value, "config", None, None)
    row = session.get(DerivedValue, (key, scope))
    if row is not None:
        try:
            value = parse(json.loads(row.value_json))
            computed_at = datetime.fromisoformat(row.computed_at)
        except ValueError, TypeError, KeyError:
            pass
        else:
            return Resolved(value, "derived", row.source, computed_at)
    return Resolved(default, "default", None, None)
