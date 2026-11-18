import shutil
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
import yt_dlp
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from fakes.clock import FakeClock
from fixtures.generator import Synth, SynthSpec, cache_key, make_video
from helpers import START, make_context, make_delivery_video
from rushes import ui
from rushes.context import Context
from rushes.db.engine import write_session

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """R20: tests never touch the network."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network access is forbidden in tests (R20)")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def _no_real_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    """R20: the socket block above does not stop yt-dlp's own clients: refuse the real thing."""

    def refused(self: object, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the real yt-dlp is forbidden in tests (R20): use a double")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "__init__", refused)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RUSHES_CONFIG_DIR", "RUSHES_DATA_DIR", "RUSHES_COMMIT", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_secrets() -> Iterator[None]:
    """Registered secrets are process-wide: keep tests independent."""
    ui.reset_secrets()
    yield
    ui.reset_secrets()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Writable copy of the repository's config, without Louis's real generic references.

    The series' `intro_outro` keys and `refs/` point at real media: tests that need references
    write their own (synthetic) ones.
    """
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target, ignore=shutil.ignore_patterns("refs"))
    for path in (target / "series").glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.pop("intro_outro", None) is not None:
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    target.mkdir()
    return target


@pytest.fixture
def edit_yaml() -> Callable[[Path, Callable[[Any], Any]], None]:
    """Load a YAML file, mutate it in place (the return value is ignored), write it back."""

    def edit(path: Path, mutate: Callable[[Any], Any]) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    return edit


@pytest.fixture
def ctx(tmp_path: Path, config_dir: Path) -> Iterator[Context]:
    """Migrated, synced context at 2026-11-16T12:00Z; events go to a StringIO (ctx_output)."""
    context = make_context(tmp_path, config_dir, clock=FakeClock(START))
    yield context
    context.close()


@pytest.fixture
def session(ctx: Context) -> Iterator[Session]:
    """ORM session on the synced `ctx` database (brands, accounts, series, creators of config/)."""
    with Session(ctx.engine) as db_session:
        yield db_session


@pytest.fixture
def wsession(ctx: Context) -> Iterator[Session]:
    """Write session (BEGIN IMMEDIATE): the only kind `assign` accepts (R1)."""
    with write_session(ctx.engine) as db_session:
        yield db_session


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, config_dir: Path, data_dir: Path) -> CliRunner:
    """Typer runner with RUSHES_CONFIG_DIR / RUSHES_DATA_DIR pointing at tmp_path."""
    monkeypatch.setenv("RUSHES_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RUSHES_DATA_DIR", str(data_dir))
    return CliRunner()


@pytest.fixture(scope="session")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Synth:
    """`synth(spec) -> Path`: a synthetic video, generated once per session for a given spec.

    The returned file is shared: copy it (helpers.drop_import) before moving or editing it.
    """
    folder = tmp_path_factory.mktemp("synth")
    cache: dict[str, Path] = {}

    def make(spec: SynthSpec) -> Path:
        key = cache_key(spec)
        if key not in cache:
            cache[key] = make_video(spec, folder / f"{key}.mp4")
        return cache[key]

    return make


@pytest.fixture(scope="session")
def delivery(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A video that satisfies `check_delivery`, made once (copy it, do not edit it)."""
    return make_delivery_video(tmp_path_factory.mktemp("delivery") / "delivery.mp4")
