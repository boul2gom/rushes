"""Console output. Only this module imports Rich (R47). Minimal for task 1; full UI in task 2."""

from collections.abc import Iterable

from rich.console import Console
from rich.markup import escape

_out = Console(highlight=False)
_err = Console(stderr=True, highlight=False)


def info(message: str) -> None:
    _out.print(escape(message), soft_wrap=True)


def warn(message: str) -> None:
    _err.print(f"[yellow]⚠ {escape(message)}[/yellow]", soft_wrap=True)


def error(message: str, *, hint: str | None = None) -> None:
    _err.print(f"[red]✗ {escape(message)}[/red]", soft_wrap=True)
    if hint:
        _err.print(escape(hint), soft_wrap=True)


def error_lines(title: str, lines: Iterable[str], *, hint: str | None = None) -> None:
    _err.print(f"[red]✗ {escape(title)}[/red]", soft_wrap=True)
    for line in lines:
        _err.print(f"  {escape(line)}", soft_wrap=True)
    if hint:
        _err.print(escape(hint), soft_wrap=True)
