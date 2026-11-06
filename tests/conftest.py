import shutil
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from fakes.clock import FakeClock
from helpers import START, make_context
from rushes import ui
from rushes.context import Context

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
    """Writable copy of the repository's example config."""
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
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
def cli_env(monkeypatch: pytest.MonkeyPatch, config_dir: Path, data_dir: Path) -> CliRunner:
    """Typer runner with RUSHES_CONFIG_DIR / RUSHES_DATA_DIR pointing at tmp_path."""
    monkeypatch.setenv("RUSHES_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RUSHES_DATA_DIR", str(data_dir))
    return CliRunner()
