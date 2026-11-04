from pathlib import Path

import pytest
from typer.testing import CliRunner

from rushes.cli import app

runner = CliRunner()

# (group, subcommands) — empty tuple means the group itself is the command.
COMMANDS: dict[str, tuple[str, ...]] = {
    "run": (),
    "status": (),
    "discover": ("youtube",),
    "market": ("collect", "analyze"),
    "ingest": (),
    "clean": (),
    "analyze": (),
    "cut": (),
    "compile": ("themes",),
    "hooks": ("propose",),
    "approve": (),
    "assign": (),
    "queue": ("rank",),
    "render": (),
    "meta": (),
    "package": (),
    "publish": ("push", "sync"),
    "stats": ("collect",),
    "posts": ("detect-published",),
    "creators": ("prospect", "snapshot", "backfill", "watch", "express", "revisit"),
    "review": ("build", "apply"),
    "vendor": ("check",),
}


def _argv() -> list[list[str]]:
    out: list[list[str]] = []
    for group, subs in COMMANDS.items():
        out.extend([[group, s] for s in subs] if subs else [[group]])
    return out


def test_help_lists_every_planned_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in COMMANDS:
        assert name in result.output


@pytest.mark.parametrize("argv", _argv(), ids=lambda a: " ".join(a))
def test_every_command_answers_help(argv: list[str]) -> None:
    result = runner.invoke(app, [*argv, "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("argv", _argv(), ids=lambda a: " ".join(a))
def test_unimplemented_command_exits_1_with_message(argv: list[str]) -> None:
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "non implémentée" in result.output


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
