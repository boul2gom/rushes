"""Execution context shared by every step (docs/plans/00-master.md section 5.1)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from sqlalchemy import Engine

from rushes.clock import Clock, SystemClock
from rushes.config import Config, Vertical, load_config
from rushes.db.engine import make_engine
from rushes.db.migrate import upgrade
from rushes.db.sync import sync_config
from rushes.models.hub import ModelHub
from rushes.net.base import NetClients, UnavailableHttp, UnavailableYtDlp
from rushes.paths import Paths
from rushes.ui import Emitter, Message, make_emitter

LOG_RETENTION_DAYS = 30

__all__ = [
    "Clock",
    "Context",
    "Filters",
    "app_commit",
    "app_version",
    "build_context",
    "rotate_logs",
]


@dataclass(frozen=True)
class Filters:
    series: str | None = None
    creator: str | None = None
    episode: int | None = None
    account: str | None = None
    limit: int | None = None
    force: bool = False
    posts: tuple[int, ...] = ()  # task 36: render, meta, package... limited to these posts
    vertical: Vertical | None = None  # task 22: vertical of the owner account's brand
    episodes: tuple[int, ...] = ()  # task 22: express-lane episodes (task 28), faces included


@dataclass(frozen=True)
class Context:
    paths: Paths
    config: Config
    engine: Engine
    ui: Emitter
    clock: Clock
    models: ModelHub  # task 5; empty before
    net: NetClients  # task 10a; doubles in tests
    environ: Mapping[str, str] | None = None  # the environment build_context was given

    def close(self) -> None:
        self.ui.close()
        self.engine.dispose()


def app_version() -> str:
    try:
        return package_version("rushes")
    except PackageNotFoundError:
        return "dev"


def app_commit(environ: Mapping[str, str] | None = None) -> str | None:
    """Set at image build time (RUSHES_COMMIT); None otherwise."""
    env = os.environ if environ is None else environ
    return env.get("RUSHES_COMMIT", "").strip() or None


def rotate_logs(logs_dir: Path, *, today: date) -> None:
    """Delete `<date>.jsonl` files older than 30 days (other files are left alone)."""
    folder = logs_dir
    if not folder.is_dir():
        return
    limit = today - timedelta(days=LOG_RETENTION_DAYS)
    for path in folder.glob("*.jsonl"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < limit:
            path.unlink(missing_ok=True)


def build_context(
    *, plain: bool, json: bool, verbose: bool, environ: Mapping[str, str] | None = None
) -> Context:
    """Load the config (ConfigError: exit 2), migrate, reload the mirrors, open today's log."""
    paths = Paths.from_env(environ)
    config = load_config(paths.config_dir)
    clock = SystemClock()
    today = clock.now().date()
    rotate_logs(paths.logs, today=today)
    ui = make_emitter(
        plain=plain,
        json=json,
        verbose=verbose,
        log_path=paths.logs / f"{today:%Y-%m-%d}.jsonl",
        clock=clock.now,
    )
    engine: Engine | None = None
    try:
        upgrade(paths.db)
        engine = make_engine(paths.db)
        for warning in sync_config(engine, config):
            ui.emit(Message("warn", warning))
    except BaseException:
        if engine is not None:
            engine.dispose()
        ui.close()
        raise
    return Context(
        paths=paths,
        config=config,
        engine=engine,
        ui=ui,
        clock=clock,
        models=ModelHub(paths, config.settings),
        net=NetClients(ytdlp=UnavailableYtDlp(), http=UnavailableHttp()),
        environ=environ,
    )
