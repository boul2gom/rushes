"""Console output: events in, Rich / plain text / JSON lines out. Only this module imports Rich.

Steps never print: they emit events (`ctx.ui.emit(...)`). A full JSON copy of every event goes to
the log file whatever the console mode is (R47: nothing secret is ever displayed or logged).
"""

import json as jsonlib
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

Level = Literal["info", "warn", "error"]
StepStatus = Literal["done", "skipped", "warned", "failed"]


@dataclass(frozen=True)
class Header:
    command: str
    scope: str | None
    test_mode: bool
    version: str
    commit: str | None


@dataclass(frozen=True)
class StepEvent:
    step: str
    item: str | None
    status: StepStatus
    duration_ms: int
    message: str = ""


@dataclass(frozen=True)
class Progress:
    task: str
    done: float
    total: float | None
    unit: str = ""


@dataclass(frozen=True)
class Message:
    level: Level
    text: str
    hint: str | None = None
    alert: str | None = None  # ntfy alert key: created here, sent by task 20


@dataclass(frozen=True)
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class Summary:
    ready_by_account: dict[str, int]
    warnings: int
    duration_ms: int
    index_path: str | None
    showcase_by_account: dict[str, int] = field(default_factory=dict)  # `ready` test posts


Event = Header | StepEvent | Progress | Message | Table | Summary


class Emitter(Protocol):
    def emit(self, event: Event) -> None: ...
    def close(self) -> None: ...


# --- redaction (R47) ---------------------------------------------------------------------------

MASK = "«***»"
_MIN_SECRET_LEN = 4  # shorter values would garble ordinary text
_SECRETS: set[str] = set()
_QUOTED = r""""[^"]*"|'[^']*'"""
# Cookie / Set-Cookie: the whole header value, `A=1; B=2; ...`
_COOKIE_PAIRS = re.compile(
    r"(?i)(?<![A-Za-z])((?:set-)?cookie)([\"']?\s*[=:]\s*)"
    rf"""(?:{_QUOTED}|["']?[^\s;"']+(?:;\s*[^\s;"']+)*)"""
)
# other credentials: quoted value (may contain spaces), `Bearer x`, or one bare word
_CREDENTIAL = re.compile(
    r"(?i)(?<![A-Za-z])(api[_-]?key|token|key|secret|passwd|password|authorization)"
    r"""([\"']?\s*[=:]\s*)"""
    rf"""(?:{_QUOTED}|["']?(?:(?:bearer|basic)\s+)?[^\s"']+)"""
)


def register_secret(value: str) -> None:
    """Every SecretStr read registers its value so that no output can show it."""
    if len(value) >= _MIN_SECRET_LEN:
        _SECRETS.add(value)


def reset_secrets() -> None:
    _SECRETS.clear()


def redact(text: str) -> str:
    """Replace registered secrets and `token=...`-like pairs by «***»."""
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, MASK)
    for pattern in (_COOKIE_PAIRS, _CREDENTIAL):
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


# --- helpers -----------------------------------------------------------------------------------

_monotonic = time.monotonic  # replaced in tests


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def format_duration(duration_ms: int) -> str:
    """61234 -> « 61,2 s » (decimal comma); under a second in ms."""
    if duration_ms < 1000:
        return f"{duration_ms} ms"
    return f"{duration_ms / 1000:.1f}".replace(".", ",") + " s"


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except AttributeError, ValueError:
        return False


_LEVEL_OF_STATUS: dict[str, Level] = {
    "done": "info",
    "skipped": "info",
    "warned": "warn",
    "failed": "error",
}


def _record(event: Event, command: str | None, moment: datetime) -> dict[str, Any]:
    """One JSON object per event (docs/plans/00-master.md section 5.2)."""
    base: dict[str, Any] = {"ts": _timestamp(moment), "level": "info", "command": command}
    match event:
        case Header():
            base |= {
                "event": "header",
                "command": event.command,
                "scope": event.scope,
                "test_mode": event.test_mode,
                "version": event.version,
                "commit": event.commit,
            }
        case StepEvent():
            base |= {
                "event": "step",
                "level": _LEVEL_OF_STATUS[event.status],
                "step": event.step,
                "item": event.item,
                "status": event.status,
                "duration_ms": event.duration_ms,
                "message": event.message,
            }
        case Progress():
            base |= {
                "event": "progress",
                "task": event.task,
                "done": event.done,
                "total": event.total,
                "unit": event.unit,
            }
        case Message():
            base |= {
                "event": "message",
                "level": event.level,
                "text": event.text,
                "hint": event.hint,
                "alert": event.alert,
            }
        case Table():
            base |= {
                "event": "table",
                "title": event.title,
                "columns": event.columns,
                "rows": event.rows,
            }
        case Summary():
            base |= {
                "event": "summary",
                "ready_by_account": event.ready_by_account,
                "warnings": event.warnings,
                "duration_ms": event.duration_ms,
                "index_path": event.index_path,
                "showcase_by_account": event.showcase_by_account,
            }
    return _redact_value(base)  # type: ignore[no-any-return]


# --- text layout shared by the plain and Rich emitters ----------------------------------------

Segment = tuple[str, str]  # (Rich style, text)
_SYMBOLS: dict[str, Segment] = {
    "done": ("green", "✓"),
    "skipped": ("dim", "↷"),
    "warned": ("yellow", "⚠"),
    "failed": ("red", "✗"),
}
NOT_PUBLISHED = "NE PAS PUBLIER"


def _header_segments(event: Header) -> list[Segment]:
    parts: list[Segment] = [("bold", f"rushes {event.command}")]
    if event.scope:
        parts.append(("", f" · {event.scope}"))
    version = f"v{event.version}" + (f" ({event.commit})" if event.commit else "")
    parts.append(("dim", f" · {version}"))
    if event.test_mode:
        parts.append(("bold red", f" · {NOT_PUBLISHED}"))
    return [(style, redact(text)) for style, text in parts]


def _lines(event: Event) -> list[list[Segment]]:
    """Lines of styled segments for every event but Header (panel) and Table."""
    match event:
        case StepEvent():
            style, symbol = _SYMBOLS[event.status]
            line: list[Segment] = [(style, symbol), ("", f" {event.step}")]
            if event.item:
                line.append(("", f" {event.item}"))
            line.append(("dim", f"  {format_duration(event.duration_ms)}"))
            if event.message:
                line.append(("", f"  {event.message}"))
            return [[(style_, redact(text)) for style_, text in line]]
        case Progress():
            total = "" if event.total is None else f"/{event.total:g}"
            unit = f" {event.unit}" if event.unit else ""
            return [[("dim", redact(f"… {event.task} {event.done:g}{total}{unit}"))]]
        case Message():
            style, symbol = {
                "info": ("", ""),
                "warn": ("yellow", "⚠ "),
                "error": ("red", "✗ "),
            }[event.level]
            out: list[list[Segment]] = [[(style, redact(f"{symbol}{event.text}"))]]
            if event.hint:
                out.append([("dim", redact(f"  → {event.hint}"))])
            return out
        case Summary():
            ready = ", ".join(f"{k} {v}" for k, v in sorted(event.ready_by_account.items()))
            head = f"Prêts : {ready or 'aucun'}"
            showcase = ", ".join(f"{k} {v}" for k, v in sorted(event.showcase_by_account.items()))
            if showcase:
                head += f" · vitrine : {showcase}"
            warn_style = "yellow" if event.warnings else "dim"
            line = [
                ("bold green", redact(head)),
                (warn_style, f" · {event.warnings} avertissement(s)"),
                ("dim", f" · {format_duration(event.duration_ms)}"),
            ]
            out = [line]
            if event.index_path:
                out.append([("", redact(f"Index : {event.index_path}"))])
            return out
        case _:
            return []


def _table_cells(event: Table) -> tuple[list[str], list[list[str]]]:
    width = len(event.columns)
    rows = [[redact(c) for c in (row + [""] * width)[:width]] for row in event.rows]
    return [redact(c) for c in event.columns], rows


def _plain_table(event: Table) -> list[str]:
    columns, rows = _table_cells(event)
    widths = [max(len(cell) for cell in col) for col in zip(columns, *rows, strict=True)]

    def fmt(cells: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    return [redact(event.title), fmt(columns), *(fmt(row) for row in rows)]


# --- emitters ----------------------------------------------------------------------------------


class JsonEmitter:
    """One JSON object per line; progress limited to one per second and per task."""

    def __init__(
        self,
        stream: TextIO,
        *,
        verbose: bool = False,
        now: Callable[[], datetime] = _utcnow,
        owns_stream: bool = False,
        throttle: bool = True,
    ) -> None:
        self.stream = stream
        self.verbose = verbose
        self._throttle = throttle
        self._now = now
        self._owns = owns_stream
        self._command: str | None = None
        self._last_progress: dict[str, float] = {}

    def _write(self, record: dict[str, Any]) -> None:
        self.stream.write(jsonlib.dumps(record, ensure_ascii=False) + "\n")
        self.stream.flush()

    def emit(self, event: Event) -> None:
        if isinstance(event, Header):
            self._command = event.command
        elif isinstance(event, Progress) and self._throttle:
            finished = event.total is not None and event.done >= event.total
            now = _monotonic()
            last = self._last_progress.get(event.task)
            if not finished and last is not None and now - last < 1.0:
                return
            if finished:
                self._last_progress.pop(event.task, None)
            else:
                self._last_progress[event.task] = now
        self._write(_record(event, self._command, self._now()))

    def trace(self, text: str) -> None:
        if self.verbose:
            self._write(
                _redact_value(
                    {
                        "ts": _timestamp(self._now()),
                        "event": "trace",
                        "level": "error",
                        "command": self._command,
                        "text": text,
                    }
                )
            )

    def close(self) -> None:
        self.stream.flush()
        if self._owns:
            self.stream.close()


class PlainEmitter:
    """Readable text without colours (--plain)."""

    def __init__(self, out: TextIO, err: TextIO, *, verbose: bool = False) -> None:
        self._out, self._err, self.verbose = out, err, verbose

    def _print(self, text: str, *, error: bool = False) -> None:
        stream = self._err if error else self._out
        stream.write(text + "\n")
        stream.flush()

    def emit(self, event: Event) -> None:
        if isinstance(event, Header):
            self._print("".join(text for _, text in _header_segments(event)))
        elif isinstance(event, Table):
            self._print("\n".join(_plain_table(event)))
        else:
            to_stderr = isinstance(event, Message) and event.level != "info"
            for line in _lines(event):
                self._print("".join(text for _, text in line), error=to_stderr)

    def trace(self, text: str) -> None:
        if self.verbose:
            self._print(redact(text), error=True)

    def close(self) -> None:
        self._out.flush()
        self._err.flush()


class RichEmitter:
    """Terminal display: fixed colours (green, yellow, red), NO_COLOR honoured, soft wrap."""

    def __init__(self, out: TextIO, err: TextIO, *, verbose: bool = False) -> None:
        no_color = bool(os.environ.get("NO_COLOR"))
        options: dict[str, Any] = {
            "color_system": None if no_color else "auto",
            "soft_wrap": True,
            "highlight": False,
            "markup": False,
            "emoji": False,
        }
        # ANSI only on a stream that really is a terminal (stderr may be redirected)
        self._out = Console(file=out, force_terminal=_is_tty(out), **options)
        self._err = (
            self._out if err is out else Console(file=err, force_terminal=_is_tty(err), **options)
        )
        self.verbose = verbose

    @staticmethod
    def _text(line: list[Segment]) -> Text:
        result = Text()
        for style, text in line:
            result.append(text, style or None)
        return result

    def emit(self, event: Event) -> None:
        if isinstance(event, Header):
            panel = Panel(self._text(_header_segments(event)), box=box.ROUNDED, expand=False)
            self._out.print(panel)
        elif isinstance(event, Table):
            columns, rows = _table_cells(event)
            table = RichTable(title=redact(event.title), box=box.SIMPLE_HEAD, title_style="bold")
            for column in columns:
                table.add_column(column, overflow="fold")
            for row in rows:
                table.add_row(*[Text(cell) for cell in row])
            self._out.print(table)
        else:
            console = (
                self._err if isinstance(event, Message) and event.level != "info" else self._out
            )
            for line in _lines(event):
                console.print(self._text(line))

    def trace(self, text: str) -> None:
        if self.verbose:
            self._err.print(Text(redact(text), style="dim"))

    def close(self) -> None:
        return None


class TeeEmitter:
    """Console emitter plus the JSON log (the log gets every event and every trace)."""

    def __init__(self, console: Emitter, log: JsonEmitter) -> None:
        self._console, self._log = console, log

    def emit(self, event: Event) -> None:
        self._console.emit(event)
        self._log.emit(event)

    def trace(self, text: str) -> None:
        self._log.trace(text)
        emit_trace(self._console, text)

    def close(self) -> None:
        self._console.close()
        self._log.close()


def emit_trace(emitter: Emitter, text: str) -> None:
    """Full trace of an error: shown with --verbose, always written to the log."""
    trace = getattr(emitter, "trace", None)
    if callable(trace):
        trace(text)


def make_emitter(
    *,
    plain: bool,
    json: bool,
    verbose: bool,
    stream: TextIO | None = None,
    log_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Emitter:
    """Rich on a TTY without flags; plain text with --plain; JSON lines otherwise. Honours NO_COLOR.

    log_path receives a full JSON copy of every event (data/logs/<date>.jsonl).
    """
    out = stream if stream is not None else sys.stdout
    err = stream if stream is not None else sys.stderr
    now = clock or _utcnow
    console: Emitter
    if json:
        console = JsonEmitter(out, verbose=verbose, now=now)
    elif plain:
        console = PlainEmitter(out, err, verbose=verbose)
    elif _is_tty(out):
        console = RichEmitter(out, err, verbose=verbose)
    else:
        console = JsonEmitter(out, verbose=verbose, now=now)
    if log_path is None:
        return console
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    return TeeEmitter(
        console, JsonEmitter(handle, verbose=True, now=now, owns_stream=True, throttle=False)
    )
