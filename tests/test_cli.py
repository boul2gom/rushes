import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from fixtures.generator import Synth, SynthSpec
from helpers import FakeStep
from rushes import cli, steps
from rushes.cli import app
from rushes.context import Filters, app_version
from rushes.errors import LockBusy
from rushes.ui import Message

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flags live on a module-level state: start every test from the defaults."""
    for flag in ("plain", "json", "verbose"):
        monkeypatch.setattr(cli.state, flag, False)


# command -> task that implements it (a string: tasks have suffixes such as "8a").
# A task that implements a command moves it from PENDING to IMPLEMENTED.
PENDING: dict[str, str] = {
    "run": "9",
    "discover youtube": "10a",
    "discover series": "38",
    "models fetch": "9",
    "models check": "9",
    "rights list": "38",
    "serve": "36",
    "market collect": "31",
    "market analyze": "31",
    "cut": "6",
    "compile themes": "30",
    "hooks propose": "14",
    "approve": "8a",
    "assign": "8a",
    "queue rank": "16b",
    "render": "7a",
    "meta": "8b",
    "package": "8b",
    "stats collect": "17",
    "posts detect-published": "17",
    "creators prospect": "27",
    "creators snapshot": "27",
    "creators backfill": "28",
    "creators watch": "28",
    "creators express": "28",
    "creators revisit": "28",
    "review build": "32",
    "review apply": "32",
}
IMPLEMENTED: tuple[str, ...] = ("status", "ingest", "clean", "analyze")


def _argv(commands: list[str]) -> list[list[str]]:
    return [name.split() for name in commands]


ALL = [*PENDING, *IMPLEMENTED]


def test_help_lists_every_planned_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in {command.split()[0] for command in ALL}:
        assert name in result.output


@pytest.mark.parametrize("argv", _argv(ALL), ids=lambda a: " ".join(a))
def test_every_command_answers_help(argv: list[str]) -> None:
    result = runner.invoke(app, [*argv, "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("command", PENDING, ids=str)
def test_unimplemented_command_exits_1_with_message(command: str) -> None:
    result = runner.invoke(app, command.split())
    assert result.exit_code == 1
    assert "non implémentée" in result.output
    assert f"tâche {PENDING[command]}" in result.output


def test_pending_and_implemented_do_not_overlap() -> None:
    assert not set(PENDING) & set(IMPLEMENTED)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "rushes" in result.output


def test_help_works_without_config_or_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUSHES_CONFIG_DIR", str(tmp_path / "absent"))
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_config_check_accepts_the_example_config(config_dir: Path, data_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 0
    assert "5 créateurs" in result.output


def test_config_check_warns_about_a_brand_without_creators(
    config_dir: Path, data_dir: Path, edit_yaml: Callable[[Path, Callable[[Any], Any]], None]
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["creators_by_brand"].update(peak_moments=[]),
    )
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 0
    assert "peak_moments" in result.output  # warning for the empty creators list


def test_invalid_config_is_refused_with_clear_message(config_dir: Path, data_dir: Path) -> None:
    (config_dir / "accounts.yaml").write_text(
        (config_dir / "accounts.yaml")
        .read_text(encoding="utf-8")
        .replace("min_s: 65", "min_s: 30"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 2
    assert "accounts.yaml" in result.output
    assert "min_s" in result.output
    assert "Traceback" not in result.output


def test_db_upgrade_twice(config_dir: Path, data_dir: Path) -> None:
    env = {"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)}
    first = runner.invoke(app, ["db", "upgrade"], env=env)
    second = runner.invoke(app, ["db", "upgrade"], env=env)
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (data_dir / "rushes.db").exists()


# --- task 2: pipeline plumbing -----------------------------------------------------------------


def _probe(step_names: tuple[str, ...]) -> typer.Typer:
    probe = typer.Typer()

    @probe.command()
    def go() -> None:
        cli.execute("probe", step_names, Filters())

    return probe


def _logs(data_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (data_dir / "logs").glob("*.jsonl"))


def test_execute_runs_the_registered_steps_and_exits_with_their_code(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setitem(steps.STEPS, "fake", FakeStep)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 0, result.output
    assert '"event": "summary"' in result.output
    assert (data_dir / "out" / "3.txt").exists()
    again = cli_env.invoke(_probe(("fake",)), [])  # R16 through the CLI: nothing left to do
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_execute_failed_item_exits_4(cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(steps.STEPS, "fake", lambda: FakeStep(fail={2}))
    assert cli_env.invoke(_probe(("fake",)), []).exit_code == 4


def test_execute_unknown_step_exits_1(cli_env: CliRunner) -> None:
    result = cli_env.invoke(_probe(("nope",)), [])
    assert result.exit_code == 1 and "nope" in result.output


def test_execute_rushes_error_uses_its_exit_code_and_hint(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy() -> FakeStep:
        raise LockBusy("verrou occupé", hint="Relance sans --no-wait.")

    monkeypatch.setitem(steps.STEPS, "fake", busy)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 3
    assert "verrou occupé" in result.output and "Relance sans --no-wait." in result.output


def test_execute_unexpected_exception_exits_1_with_trace_only_in_the_log(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 1
    assert "kaboom" in result.output and "Traceback" not in result.output
    assert "Traceback" in _logs(data_dir)


def test_verbose_flag_prints_the_trace(cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    monkeypatch.setattr(cli.state, "verbose", True)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 1 and "Traceback" in result.output


def test_filters_from_options() -> None:
    filters = cli.make_filters(
        series="tom_and_jerry", creator=None, episode="e_000045", account="a", limit=3, force=True
    )
    assert filters == Filters(series="tom_and_jerry", episode=45, account="a", limit=3, force=True)
    assert cli.make_filters(episode="12").episode == 12
    with pytest.raises(typer.BadParameter):
        cli.make_filters(episode="s_000001")
    with pytest.raises(typer.BadParameter):
        cli.make_filters(episode="abc")


def test_status_does_not_open_a_run(cli_env: CliRunner, data_dir: Path) -> None:
    assert cli_env.invoke(app, ["status"]).exit_code == 0
    con = sqlite3.connect(data_dir / "rushes.db")
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    con.close()


def test_config_check_and_db_upgrade_emit_messages(cli_env: CliRunner) -> None:
    check = cli_env.invoke(app, ["--json", "config", "check"])
    messages = [json.loads(line) for line in check.output.splitlines() if line.startswith("{")]
    assert messages and all(m["event"] == "message" for m in messages)
    upgrade = cli_env.invoke(app, ["--json", "db", "upgrade"])
    assert json.loads(upgrade.output.splitlines()[-1])["event"] == "message"


def test_version_goes_through_the_emitter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Recorder:
        def emit(self, event: Any) -> None:
            seen.append(event)

        def close(self) -> None:
            pass

    def fake_make_emitter(**kwargs: Any) -> Recorder:
        seen.append(kwargs)
        return Recorder()

    monkeypatch.setattr(cli, "app_version", lambda: "9.9.9")
    monkeypatch.setattr(cli, "make_emitter", fake_make_emitter)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output == ""  # nothing printed outside the emitter
    assert seen == [
        {"plain": True, "json": False, "verbose": False},
        Message("info", "rushes 9.9.9"),
    ]


def test_version_prints_the_real_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"rushes {app_version()}"


def test_usage_error_inside_a_command_is_a_clean_message_not_an_unexpected_error() -> None:
    result = runner.invoke(app, ["ingest", "--episode", "abc"])
    assert result.exit_code == 1
    assert "épisode invalide" in result.output
    assert "inattendue" not in result.output and "Traceback" not in result.output


def test_unexpected_error_before_any_log_does_not_point_to_a_log_file(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "load_config", broken)
    result = cli_env.invoke(app, ["config", "check"])
    assert result.exit_code == 1 and "kaboom" in result.output
    assert "data/logs" not in result.output


def test_unexpected_error_after_the_log_is_open_points_to_it(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert "data/logs" in result.output


# --- task 4: ingest and clean -------------------------------------------------------------------


def test_ingest_then_clean_commands_run_the_steps(
    cli_env: CliRunner, data_dir: Path, synth: Synth
) -> None:
    folder = data_dir / "import" / "tom_and_jerry"
    folder.mkdir(parents=True)
    video = synth(SynthSpec(duration_ms=3000, size=(64, 36), black_ms=[(0, 2500)]))
    (folder / "a.mp4").write_bytes(video.read_bytes())
    os.utime(folder / "a.mp4", (1_700_000_000, 1_700_000_000))  # not "being copied"

    ingest = cli_env.invoke(app, ["--json", "ingest"])
    assert ingest.exit_code == 0, ingest.output
    assert (
        '"step": "ingest"' in ingest.output and (data_dir / "import" / "_done" / "a.mp4").exists()
    )

    clean = cli_env.invoke(app, ["--json", "clean", "--limit", "1"])
    assert clean.exit_code == 0, clean.output
    assert '"step": "clean"' in ingest.output + clean.output
    assert list((data_dir / "episodes").glob("e_*/clean.mp4"))

    again = cli_env.invoke(app, ["--json", "clean"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_ingest_command_with_an_unknown_folder_exits_0_with_a_warning(
    cli_env: CliRunner, data_dir: Path
) -> None:
    stray = data_dir / "import" / "nobody" / "a.mp4"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    os.utime(stray, (1_700_000_000, 1_700_000_000))
    result = cli_env.invoke(app, ["ingest"])
    assert result.exit_code == 0 and "nobody" in result.output


def test_steps_registry_has_ingest_and_clean() -> None:
    assert {"ingest", "clean"} <= set(steps.STEPS)
    assert steps.RUN_ORDER[:2] == ("ingest", "clean")


# --- task 5: analyze ----------------------------------------------------------------------------


def test_analyze_command_runs_the_step_and_is_idempotent(
    cli_env: CliRunner, data_dir: Path, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.models import FakeVad
    from rushes.models import hub

    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "vad", FakeVad)  # no real model in tests
    folder = data_dir / "import" / "tom_and_jerry"
    folder.mkdir(parents=True)
    video = synth(SynthSpec(duration_ms=3000, size=(64, 36), fps=10))
    (folder / "a.mp4").write_bytes(video.read_bytes())
    os.utime(folder / "a.mp4", (1_700_000_000, 1_700_000_000))
    assert cli_env.invoke(app, ["--json", "ingest"]).exit_code == 0
    assert cli_env.invoke(app, ["--json", "clean"]).exit_code == 0

    analyze = cli_env.invoke(app, ["--json", "analyze"])
    assert analyze.exit_code == 0, analyze.output
    assert '"step": "analyze"' in analyze.output
    assert list((data_dir / "episodes").glob("e_*/scenes.json"))

    again = cli_env.invoke(app, ["--json", "analyze"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output
