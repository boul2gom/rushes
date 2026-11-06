import sqlite3
from pathlib import Path

import pytest

from rushes.db.migrate import current_revision, upgrade


def columns(db: Path, table: str) -> dict[str, str]:
    con = sqlite3.connect(db)
    try:
        return {row[1]: row[2] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_migration_0003_adds_exit_code_and_status_check(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db, "0002")
    con = sqlite3.connect(db)
    con.execute("INSERT INTO runs(command, started_at, status) VALUES ('old', 't', 'ok')")
    con.execute("INSERT INTO runs(command, started_at, status) VALUES ('odd', 't', 'weird')")
    con.commit()
    con.close()
    upgrade(db)
    assert current_revision(db) == "0003"
    assert columns(db, "runs")["exit_code"] == "INTEGER"
    con = sqlite3.connect(db)
    statuses = dict(con.execute("SELECT command, status FROM runs"))
    assert statuses == {"old": "ok", "odd": "failed"}  # unknown status mapped, rows kept
    for status in ("running", "ok", "partial", "failed"):
        con.execute("INSERT INTO runs(command, started_at, status) VALUES ('x', 't', ?)", (status,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO runs(command, started_at, status) VALUES ('x', 't', 'bogus')")
    # autoincrement survives the table rebuild
    ids = [r[0] for r in con.execute("SELECT id FROM runs")]
    assert ids == sorted(set(ids))
    con.close()
