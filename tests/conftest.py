import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """R20: tests never touch the network."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network access is forbidden in tests (R20)")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RUSHES_CONFIG_DIR", "RUSHES_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


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
