"""Derived values: config > derived > default, with a history line per record (master 5.7)."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from helpers import START
from rushes.db.derived import Resolved, record, resolve
from rushes.db.models import DerivedValue, DerivedValueHistory


def _tags(raw: object) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise ValueError("liste de chaînes attendue")
    return list(raw)


def _resolve(session: Session, config_value: list[str] | None = None) -> Resolved[list[str]]:
    return resolve(
        session, "hashtags.pool", "tom_and_jerry", config_value=config_value, default=["#d"],
        parse=_tags,
    )  # fmt: skip


def _history(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(DerivedValueHistory)) or 0


def test_derived_resolve_order_and_history(session: Session) -> None:
    default = _resolve(session)
    assert (default.value, default.origin, default.source, default.computed_at) == (
        ["#d"], "default", None, None,
    )  # fmt: skip

    record(session, "hashtags.pool", "tom_and_jerry", ["#a"], source="market", confidence=0.5,
           at=START)  # fmt: skip
    derived = _resolve(session)
    assert (derived.value, derived.origin, derived.source) == (["#a"], "derived", "market")
    assert derived.computed_at == START

    configured = _resolve(session, config_value=["#c"])  # config wins over the derived value
    assert (configured.value, configured.origin) == (["#c"], "config")

    later = START + timedelta(days=1)
    record(session, "hashtags.pool", "tom_and_jerry", ["#b"], source="market", confidence=0.9,
           at=later)  # fmt: skip
    assert _resolve(session).value == ["#b"]
    assert _history(session) == 2  # one line per record
    rows = session.scalars(select(DerivedValue)).all()
    assert len(rows) == 1  # the table keeps the current value only
    assert json.loads(rows[0].value_json) == ["#b"]
    assert rows[0].confidence == 0.9
    lines = session.scalars(select(DerivedValueHistory).order_by(DerivedValueHistory.id)).all()
    assert [json.loads(line.value_json) for line in lines] == [["#a"], ["#b"]]


def test_derived_scopes_and_keys_are_independent(session: Session) -> None:
    record(session, "hashtags.pool", "oggy", ["#o"], source="s", confidence=1, at=START)
    assert _resolve(session).origin == "default"  # tom_and_jerry has nothing
    other = resolve(session, "hashtags.pool", "oggy", config_value=None, default=[], parse=_tags)
    assert other.value == ["#o"]


def test_derived_empty_config_list_still_wins_when_given(session: Session) -> None:
    record(session, "hashtags.pool", "tom_and_jerry", ["#a"], source="s", confidence=1, at=START)
    assert _resolve(session, config_value=[]).origin == "config"


def test_derived_unparsable_value_falls_back_to_the_default(session: Session) -> None:
    record(session, "hashtags.pool", "tom_and_jerry", {"not": "a list"}, source="s",
           confidence=1, at=START)  # fmt: skip
    resolved = _resolve(session)
    assert (resolved.value, resolved.origin) == (["#d"], "default")


def test_derived_record_stores_utc_and_refuses_naive_dates(session: Session) -> None:
    paris = timezone(timedelta(hours=1))
    record(
        session,
        "k",
        "global",
        1,
        source="s",
        confidence=1,
        at=datetime(2026, 1, 1, 1, tzinfo=paris),
    )
    row = session.get_one(DerivedValue, ("k", "global"))
    assert row.computed_at == "2026-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="fuseau"):
        record(session, "k", "global", 1, source="s", confidence=1, at=datetime(2026, 1, 1))


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan")])
def test_derived_record_refuses_confidence_outside_0_1(session: Session, confidence: float) -> None:
    with pytest.raises(ValueError, match="confiance"):
        record(session, "k", "global", 1, source="s", confidence=confidence, at=START)
    assert _history(session) == 0


def test_derived_record_refuses_values_json_cannot_hold(session: Session) -> None:
    with pytest.raises(ValueError):
        record(session, "k", "global", {1, 2}, source="s", confidence=1, at=START)
    assert _history(session) == 0


def test_derived_resolved_is_frozen() -> None:
    resolved = Resolved(1, "default", None, None)
    with pytest.raises(AttributeError):
        resolved.value = 2  # type: ignore[misc]


def test_derived_utc_date_is_read_back_as_aware(session: Session) -> None:
    record(session, "k", "global", 3, source="s", confidence=1, at=START.astimezone(UTC))
    got = resolve(session, "k", "global", config_value=None, default=0, parse=int)
    assert got.computed_at is not None and got.computed_at.tzinfo is not None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), [1.0, float("nan")]])
def test_derived_record_refuses_nan_and_infinity_in_french(session: Session, bad: object) -> None:
    with pytest.raises(ValueError, match="non sérialisable"):
        record(session, "k", "global", bad, source="s", confidence=1, at=START)
    assert _history(session) == 0


def test_derived_resolve_returns_the_recorded_confidence(session: Session) -> None:
    """One read gives the value and its confidence (a list carried over keeps it)."""
    assert _resolve(session).confidence is None
    record(session, "hashtags.pool", "tom_and_jerry", ["#a"], source="market", confidence=0.7,
           at=START)  # fmt: skip
    assert _resolve(session).confidence == 0.7
    assert _resolve(session, config_value=["#c"]).confidence is None
