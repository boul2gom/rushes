"""Step contract and idempotent runner (docs/plans/00-master.md section 5.3).

A step selects the work not done yet, runs it item by item (files first through `atomic_path`, then
one database transaction) and reports an `Outcome`. The runner counts, emits events, keeps one
`runs` row per command and turns everything into an exit code.
"""

import json
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Protocol

from sqlalchemy import text

from rushes.context import Context, Filters, app_commit, app_version
from rushes.errors import (
    EXIT_ERROR,
    EXIT_INVALID_CONFIG,
    EXIT_LOCK_BUSY,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_SOURCE_UNAVAILABLE,
    LockBusy,
    RushesError,
)
from rushes.ids import episode_key
from rushes.lock import HeavyLock, heavy_lock
from rushes.ui import Header, Message, StepEvent, StepStatus, Summary, emit_trace, redact

# 2 > 3 > 1 > 4 > 5 > 0
_PRIORITY = (
    EXIT_INVALID_CONFIG,
    EXIT_LOCK_BUSY,
    EXIT_ERROR,
    EXIT_PARTIAL,
    EXIT_SOURCE_UNAVAILABLE,
    EXIT_OK,
)


@dataclass(frozen=True)
class Outcome:
    status: StepStatus
    message: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    source_unavailable: bool = False  # external source lost (network, YouTube, TikTok): code 5


class Step[T](Protocol):
    name: ClassVar[str]  # "render", "cut", ...
    heavy: ClassVar[bool]  # True: takes the heavy lock

    def key(self, item: T) -> str: ...  # displayed identifier, e.g. "p_000123"
    def select(self, ctx: Context, filters: Filters) -> Iterable[T]: ...  # work not done yet
    def run(self, ctx: Context, item: T, *, force: bool) -> Outcome: ...  # files, then the database


@dataclass(frozen=True)
class StepReport:
    step: str
    done: int
    skipped: int
    warned: int
    failed: int
    duration_ms: int
    unavailable: int = 0  # items whose Outcome has source_unavailable=True


class BaseStep:
    """Default implementation of the optional parts of Step."""

    heavy: ClassVar[bool] = False

    def finish(self, ctx: Context) -> None:
        """Called once after the last item (e.g. INDEX.html); no-op by default."""


def combine_exit_codes(*codes: int) -> int:
    """The highest-priority code (2 > 3 > 1 > 4 > 5 > 0); an unknown code counts as 1."""
    known = {EXIT_OK, *_PRIORITY}
    normalized = {code if code in known else EXIT_ERROR for code in codes}
    return next((code for code in _PRIORITY if code in normalized), EXIT_OK)


def exit_code_for(reports: Sequence[StepReport], errors: Sequence[RushesError]) -> int:
    codes = [error.exit_code for error in errors]
    if any(report.failed for report in reports):
        codes.append(EXIT_PARTIAL)
    if any(report.unavailable for report in reports):
        codes.append(EXIT_SOURCE_UNAVAILABLE)
    return combine_exit_codes(*codes)


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _short(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}")


def _execute_step[T](
    ctx: Context, step: Step[T], filters: Filters, yield_lock: HeavyLock | None
) -> tuple[StepReport, list[RushesError]]:
    """Run a step; errors raised outside the items (select, finish) are returned, not raised."""
    started = time.monotonic()
    counts = {"done": 0, "skipped": 0, "warned": 0, "failed": 0}
    unavailable = 0
    errors: list[RushesError] = []

    def report() -> StepReport:
        return StepReport(step.name, duration_ms=_ms(started), unavailable=unavailable, **counts)

    def failed(item: str | None, exc: Exception, began: float) -> None:
        counts["failed"] += 1
        ctx.ui.emit(StepEvent(step.name, item, "failed", _ms(began), _short(exc)))
        emit_trace(ctx.ui, traceback.format_exc())

    try:
        items = list(step.select(ctx, filters))
    except RushesError as exc:
        errors.append(exc)
        return report(), errors
    except Exception as exc:
        failed(None, exc, started)
        return report(), errors
    if filters.limit is not None:
        items = items[: max(filters.limit, 0)]

    for index, item in enumerate(items):
        if index and yield_lock is not None:
            yield_lock.yield_if_express_waiting(before_yield=ctx.models.release)
        began = time.monotonic()
        key = "?"
        try:
            key = step.key(item)
            outcome = step.run(ctx, item, force=filters.force)
        except Exception as exc:
            failed(key, exc, began)
            continue
        counts[outcome.status] += 1
        unavailable += outcome.source_unavailable
        ctx.ui.emit(StepEvent(step.name, key, outcome.status, _ms(began), outcome.message))

    finish = getattr(step, "finish", None)
    if callable(finish):
        began = time.monotonic()
        try:
            finish(ctx)
        except RushesError as exc:
            errors.append(exc)
        except Exception as exc:
            failed(None, exc, began)
    return report(), errors


def run_step[T](ctx: Context, step: Step[T], filters: Filters) -> StepReport:
    """Run one step on its own (no lock, no hand-over to the express lane).

    An error raised outside the items (SourceUnavailable in `select`...) is re-raised after the
    report is built; `run_pipeline` collects it instead.
    """
    step_report, errors = _execute_step(ctx, step, filters, None)
    if errors:
        raise errors[0]
    return step_report


# --- pipeline ----------------------------------------------------------------------------------


def _scope(filters: Filters) -> str | None:
    parts = [
        f"série {filters.series}" if filters.series else "",
        f"créateur {filters.creator}" if filters.creator else "",
        f"épisode {episode_key(filters.episode)}" if filters.episode is not None else "",
        f"compte {filters.account}" if filters.account else "",
    ]
    return " · ".join(part for part in parts if part) or None


def _now_iso(ctx: Context) -> str:
    return ctx.clock.now().isoformat(timespec="seconds")


def _open_run(ctx: Context, command: str) -> int:
    with ctx.engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO runs(command, started_at, status) VALUES (:c, :s, 'running')"),
            {"c": command, "s": _now_iso(ctx)},
        )
        return int(result.lastrowid or 0)


def _run_status(code: int) -> str:
    if code == EXIT_OK:
        return "ok"
    return "partial" if code in (EXIT_PARTIAL, EXIT_SOURCE_UNAVAILABLE) else "failed"


def _close_run(
    ctx: Context,
    run_id: int,
    code: int,
    reports: Sequence[StepReport],
    errors: Sequence[RushesError],
) -> None:
    summary = {
        "reports": [asdict(report) for report in reports],
        "errors": [
            {"type": type(e).__name__, "message": redact(str(e)), "exit_code": e.exit_code}
            for e in errors
        ],
    }
    with ctx.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE runs SET ended_at=:e, status=:st, summary_json=:j, exit_code=:c "
                "WHERE id=:id"
            ),
            {
                "e": _now_iso(ctx),
                "st": _run_status(code),
                "j": json.dumps(summary, ensure_ascii=False),
                "c": code,
                "id": run_id,
            },
        )


def _ready_by_account(ctx: Context) -> dict[str, int]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT account_id, count(*) FROM posts WHERE status='ready' AND is_test=0 "
                "GROUP BY account_id ORDER BY account_id"
            )
        ).all()
    return {str(account): int(count) for account, count in rows}


def _report_error(ctx: Context, error: RushesError) -> None:
    level = "warn" if error.exit_code == EXIT_SOURCE_UNAVAILABLE else "error"
    ctx.ui.emit(Message(level, str(error) or type(error).__name__, hint=error.hint))


def run_pipeline(
    ctx: Context,
    steps: Sequence[Step[Any]],
    filters: Filters,
    *,
    command: str,
    lock: HeavyLock | None = None,
    yield_to_express: bool = True,
    wait: bool = True,
) -> int:
    """Run the steps in order inside one `runs` row; returns the exit code.

    The heavy lock is taken once for the whole pipeline when a step is heavy (unless `lock` is
    given: the caller already holds it). Heavy steps cede it to the express lane between two items
    only when `yield_to_express`. `wait=False` turns a busy lock into LockBusy (code 3). Whatever
    escapes (even Ctrl-C) closes the `runs` row as failed first.
    """
    started = time.monotonic()
    run_id = _open_run(ctx, command)
    ctx.ui.emit(Header(command, _scope(filters), False, app_version(), app_commit(ctx.environ)))
    reports: list[StepReport] = []
    errors: list[RushesError] = []
    try:
        with ExitStack() as stack:
            held = lock
            todo = list(steps)
            try:
                if held is None and any(step.heavy for step in steps):
                    held = stack.enter_context(heavy_lock(ctx.paths, wait=wait))
            except LockBusy as exc:
                errors.append(exc)
                _report_error(ctx, exc)
                todo = []
            for step in todo:
                yield_lock = held if (yield_to_express and step.heavy) else None
                step_report, step_errors = _execute_step(ctx, step, filters, yield_lock)
                reports.append(step_report)
                errors.extend(step_errors)
                for error in step_errors:
                    _report_error(ctx, error)
    except BaseException:
        _close_run(ctx, run_id, EXIT_ERROR, reports, errors)
        raise
    code = exit_code_for(reports, errors)
    _close_run(ctx, run_id, code, reports, errors)
    warnings = sum(r.warned for r in reports) + sum(
        e.exit_code == EXIT_SOURCE_UNAVAILABLE for e in errors
    )
    index = ctx.paths.outbox / "INDEX.html"
    ctx.ui.emit(
        Summary(
            _ready_by_account(ctx), warnings, _ms(started), str(index) if index.exists() else None
        )
    )
    return code
