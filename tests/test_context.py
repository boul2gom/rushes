import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from fakes.clock import FakeClock
from helpers import START, make_context, snapshot
from rushes import context
from rushes.config import ConfigError, load_config
from rushes.context import Filters, build_context, rotate_logs
from rushes.errors import SourceUnavailable
from rushes.models.hub import ModelHub
from rushes.net.base import NetClients, UnavailableHttp, UnavailableYtDlp
from rushes.paths import Paths
from rushes.runner import run_pipeline
from rushes.ui import Message


def env(config_dir: Path, data_dir: Path) -> dict[str, str]:
    return {"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)}


def test_build_context_migrates_syncs_and_opens_the_log(config_dir: Path, data_dir: Path) -> None:
    ctx = build_context(plain=False, json=True, verbose=False, environ=env(config_dir, data_dir))
    try:
        assert ctx.paths.data_dir == data_dir.resolve()
        assert ctx.paths.db.exists()
        assert isinstance(ctx.models, ModelHub) and isinstance(ctx.net, NetClients)
        ctx.ui.emit(Message("info", "bonjour"))
    finally:
        ctx.close()
    (log,) = (data_dir / "logs").glob("*.jsonl")
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["text"] == "bonjour"
    assert log.name == f"{datetime.now(UTC):%Y-%m-%d}.jsonl"


def test_build_context_with_invalid_config_raises_config_error(
    config_dir: Path, data_dir: Path
) -> None:
    (config_dir / "settings.yaml").write_text("rate_limit_s: -1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        build_context(plain=False, json=True, verbose=False, environ=env(config_dir, data_dir))


def test_build_context_twice_changes_no_row(config_dir: Path, data_dir: Path) -> None:
    snaps = []
    for _ in range(2):
        ctx = build_context(
            plain=False, json=True, verbose=False, environ=env(config_dir, data_dir)
        )
        snaps.append(snapshot(ctx, include_runs=True))
        ctx.close()
    assert snaps[0] == snaps[1]
    assert snaps[0]["table:brands"]  # the mirrors were really loaded
    assert len(list((data_dir / "logs").glob("*.jsonl"))) == 1


def test_header_commit_uses_the_environ_given_to_build_context(
    config_dir: Path, data_dir: Path
) -> None:
    environ = {**env(config_dir, data_dir), "RUSHES_COMMIT": "abc1234"}
    ctx = build_context(plain=False, json=True, verbose=False, environ=environ)
    try:
        run_pipeline(ctx, [], Filters(), command="t")
    finally:
        ctx.close()
    (log,) = (data_dir / "logs").glob("*.jsonl")
    header = next(
        r
        for r in map(json.loads, log.read_text(encoding="utf-8").splitlines())
        if r["event"] == "header"
    )
    assert header["commit"] == "abc1234"


def test_engine_is_disposed_when_the_mirror_sync_fails(
    config_dir: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[bool] = []
    real_make_engine = context.make_engine

    def spy(db_path: Path) -> Engine:
        engine = real_make_engine(db_path)
        original = engine.dispose
        engine.dispose = lambda *a, **k: (disposed.append(True), original(*a, **k))[1]  # type: ignore[method-assign]
        return engine

    def failing_sync(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("sync exploded")

    monkeypatch.setattr(context, "make_engine", spy)
    monkeypatch.setattr(context, "sync_config", failing_sync)
    with pytest.raises(RuntimeError, match="sync exploded"):
        build_context(plain=False, json=True, verbose=False, environ=env(config_dir, data_dir))
    assert disposed == [True]


def test_rotate_logs_removes_files_older_than_30_days(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("2026-09-01", "2026-10-16", "2026-10-17", "2026-11-16"):
        (logs / f"{name}.jsonl").write_text("{}\n")
    (logs / "notes.txt").write_text("keep")
    rotate_logs(logs, today=date(2026, 11, 16))
    assert sorted(p.name for p in logs.iterdir()) == [
        "2026-10-17.jsonl",
        "2026-11-16.jsonl",
        "notes.txt",
    ]
    rotate_logs(tmp_path / "absent", today=date(2026, 11, 16))  # no folder: nothing to do


def test_header_commit_comes_from_rushes_commit() -> None:
    from rushes.context import app_commit

    assert app_commit({}) is None
    assert app_commit({"RUSHES_COMMIT": "abc1234"}) == "abc1234"
    assert app_commit({"RUSHES_COMMIT": " "}) is None


def test_unavailable_net_clients_raise_source_unavailable(tmp_path: Path) -> None:
    ytdlp, http = UnavailableYtDlp(), UnavailableHttp()
    calls = (
        lambda: ytdlp.extract("u", flat=True, platform="youtube"),
        lambda: ytdlp.download("u", tmp_path, max_height=720),
        lambda: http.get_json("u"),
        lambda: http.get_text("u"),
        lambda: http.post("u", body=b"", headers={}),
        lambda: http.download("u", tmp_path / "f"),
    )
    for call in calls:
        with pytest.raises(SourceUnavailable, match="tâche 10a"):
            call()


def test_model_hub_loads_once_and_release_forgets(tmp_path: Path, config_dir: Path) -> None:
    made: list[str] = []

    def factory() -> object:
        made.append("clip")
        return object()

    paths = Paths(config_dir=config_dir, data_dir=tmp_path)
    hub = ModelHub(paths, load_config(config_dir).settings, factories={"clip": factory})
    assert hub.loads() == {}
    assert hub.get("clip") is hub.get("clip")
    assert hub.loads() == {"clip": 1} and made == ["clip"]
    hub.release()
    hub.get("clip")
    assert hub.loads() == {"clip": 2}  # reloaded after a release
    with pytest.raises(KeyError):
        hub.get("unknown")


def test_make_context_helper_uses_the_fake_clock(tmp_path: Path, config_dir: Path) -> None:
    clock = FakeClock(START)
    ctx = make_context(tmp_path, config_dir, clock=clock)
    assert ctx.clock.now() == START
    clock.advance(hours=1)
    assert ctx.clock.now().hour == 13
    ctx.close()
