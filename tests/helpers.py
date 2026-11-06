"""Shared test helpers: context factory, state snapshot, a configurable step."""

import hashlib
import io
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import text

from fakes.clock import FakeClock
from fakes.models import FakeModelHub
from fakes.net import FakeHttp, FakeYtDlp
from rushes.config import load_config
from rushes.context import Context, Filters
from rushes.db.engine import make_engine
from rushes.db.migrate import upgrade
from rushes.db.sync import sync_config
from rushes.fsutil import atomic_path
from rushes.net.base import NetClients
from rushes.paths import Paths
from rushes.runner import BaseStep, Outcome
from rushes.ui import make_emitter

START = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def make_context(
    tmp_path: Path,
    config_dir: Path,
    *,
    clock: FakeClock | None = None,
    hub: Any = None,
    net: NetClients | None = None,
) -> Context:
    """Migrated and synced database in tmp_path; JSON events go to a StringIO (ctx_output)."""
    paths = Paths(config_dir=config_dir, data_dir=tmp_path / "data")
    clock = clock or FakeClock(START)
    config = load_config(config_dir)
    upgrade(paths.db)
    engine = make_engine(paths.db)
    sync_config(engine, config)
    ui = make_emitter(plain=False, json=True, verbose=False, stream=io.StringIO(), clock=clock.now)
    return Context(
        paths=paths,
        config=config,
        engine=engine,
        ui=ui,
        clock=clock,
        models=hub or FakeModelHub(),
        net=net or NetClients(ytdlp=FakeYtDlp(), http=FakeHttp()),
    )


def ctx_output(ctx: Context) -> str:
    stream = ctx.ui.stream  # type: ignore[attr-defined]
    return str(stream.getvalue())


def ctx_events(ctx: Context) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ctx_output(ctx).splitlines() if line.strip()]


def snapshot(ctx: Context, *, include_runs: bool = False) -> dict[str, object]:
    """Every table (sorted rows) + {relative path: sha256} of data/.

    The database files, `data/logs` and (unless `include_runs`) the `runs` table are left out:
    a command always writes its own run row and log lines, which is not "work" (R16).
    """
    state: dict[str, object] = {}
    with ctx.engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).scalars()
        for name in sorted(tables):
            if name == "runs" and not include_runs:
                continue
            rows = conn.execute(text(f'SELECT * FROM "{name}"')).all()
            state[f"table:{name}"] = sorted(repr(tuple(row)) for row in rows)
    files: dict[str, str] = {}
    root = ctx.paths.data_dir
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not path.name.startswith("rushes.db") and relative.parts[0] != "logs":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    state["files"] = files
    return state


class FakeStep(BaseStep):
    """Items [1, 2, 3]: writes data/out/<n>.txt then a runs_test row; select skips the done ones."""

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        *,
        items: Iterable[int] = (1, 2, 3),
        fail: Iterable[int] = (),
        skip: Iterable[int] = (),
        crash_during_write: Iterable[int] = (),
        heavy: bool = False,
        outcomes: Mapping[int, Outcome] | None = None,
        on_run: Any = None,
    ) -> None:
        self.items = tuple(items)
        self.fail = set(fail)
        self.skip = set(skip)
        self.crash_during_write = set(crash_during_write)
        self.outcomes = dict(outcomes or {})
        self.on_run = on_run
        self.heavy = heavy  # type: ignore[misc]
        self.finished = 0
        self.ran: list[int] = []

    def key(self, item: int) -> str:
        return f"i_{item}"

    def _ensure_table(self, ctx: Context) -> None:
        with ctx.engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS runs_test(n INTEGER PRIMARY KEY)"))

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        self._ensure_table(ctx)
        if filters.force:
            return list(self.items)
        with ctx.engine.connect() as conn:
            done = set(conn.execute(text("SELECT n FROM runs_test")).scalars())
        return [i for i in self.items if i not in done]

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        self.ran.append(item)
        if self.on_run is not None:
            self.on_run(item)
        if item in self.outcomes:
            return self.outcomes[item]
        if item in self.skip:
            return Outcome("skipped", "déjà fait")
        if item in self.fail:
            raise RuntimeError(f"boom {item}")
        out = ctx.paths.data_dir / "out" / f"{item}.txt"
        with atomic_path(out) as tmp:
            tmp.write_text(f"item {item}", encoding="utf-8")
            if item in self.crash_during_write:
                raise RuntimeError("killed while writing")
        with ctx.engine.begin() as conn:
            conn.execute(text("INSERT OR REPLACE INTO runs_test(n) VALUES (:n)"), {"n": item})
        return Outcome("done")

    def finish(self, ctx: Context) -> None:
        self.finished += 1
