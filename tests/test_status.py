import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from rushes.cli import app


def tables(output: str) -> list[dict[str, object]]:
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [r for r in records if r["event"] == "table"]


def insert_run(db: Path, command: str, started: str, status: str, code: int | None) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO runs(command, started_at, ended_at, status, exit_code) VALUES (?,?,?,?,?)",
        (command, started, None if status == "running" else started, status, code),
    )
    con.commit()
    con.close()


def test_status_on_empty_db_exits_0_with_four_tables(cli_env: CliRunner) -> None:
    result = cli_env.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    found = tables(result.output)
    assert [t["title"] for t in found] == ["Derniers runs", "File par compte", "Alertes", "Disque"]
    queue = found[1]
    assert queue["rows"] and all(row[1] == "0" for row in queue["rows"])  # accounts, nothing ready
    assert found[2]["rows"] == [["✓", "Aucune alerte"]]
    assert len(found[3]["rows"]) == 1


def test_status_lists_last_run_per_command_and_failed_as_alert(
    cli_env: CliRunner, data_dir: Path
) -> None:
    assert cli_env.invoke(app, ["db", "upgrade"]).exit_code == 0
    db = data_dir / "rushes.db"
    insert_run(db, "run", "2026-11-14T02:00:00+00:00", "ok", 0)
    insert_run(db, "run", "2026-11-15T02:00:00+00:00", "failed", 1)
    insert_run(db, "ingest", "2026-11-15T03:00:00+00:00", "ok", 0)
    insert_run(db, "render", "2026-11-15T04:00:00+00:00", "partial", 4)
    result = cli_env.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    runs, _, alerts, _ = tables(result.output)
    by_command = {row[0]: row for row in runs["rows"]}  # type: ignore[attr-defined]
    assert set(by_command) == {"run", "ingest", "render"}
    assert "2026-11-15" in by_command["run"][1] and "failed" in by_command["run"][2]
    flat = " ".join(" ".join(row) for row in alerts["rows"])  # type: ignore[attr-defined]
    assert "run" in flat and "ingest" not in flat and "Aucune alerte" not in flat


def test_status_counts_ready_posts_per_account(cli_env: CliRunner, data_dir: Path) -> None:
    assert cli_env.invoke(app, ["db", "upgrade"]).exit_code == 0
    con = sqlite3.connect(data_dir / "rushes.db")
    account = con.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()[0]
    for status, is_test in (("ready", 0), ("ready", 0), ("ready", 1), ("rendered", 0)):
        con.execute(
            "INSERT INTO posts(account_id, status, is_test) VALUES (?,?,?)",
            (account, status, is_test),
        )
    con.commit()
    con.close()
    _, queue, _, _ = tables(cli_env.invoke(app, ["status"]).output)
    row = next(r for r in queue["rows"] if r[0] == account)  # type: ignore[attr-defined]
    assert row[1:] == ["2", "1", "1"]  # ready, showcase, in progress


def test_status_json_flag_and_plain_flag(cli_env: CliRunner) -> None:
    as_json = cli_env.invoke(app, ["--json", "status"])
    assert tables(as_json.output)
    plain = cli_env.invoke(app, ["--plain", "status"])
    assert plain.exit_code == 0 and "Derniers runs" in plain.output and "\x1b[" not in plain.output


def test_status_with_invalid_config_exits_2(cli_env: CliRunner, config_dir: Path) -> None:
    (config_dir / "accounts.yaml").write_text("not: [valid", encoding="utf-8")
    result = cli_env.invoke(app, ["status"])
    assert result.exit_code == 2
    assert "accounts.yaml" in result.output
