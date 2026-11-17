import sqlite3
from pathlib import Path

import pytest

from rushes.db.migrate import current_revision, upgrade

EPISODE = (
    "INSERT INTO episodes(series_id,provenance,owner_account_id,file_path,sha256,duration_ms,"
    "status{extra}) VALUES ('s','local','a','p.mp4',?,1000,?{marks})"
)


def _insert(con: sqlite3.Connection, sha: str, status: str = "imported", **columns: object) -> None:
    names = "".join(f",{name}" for name in columns)
    marks = ",?" * len(columns)
    con.execute(EPISODE.format(extra=names, marks=marks), (sha, status, *columns.values()))


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    con.execute("INSERT INTO brands VALUES ('b','cartoons','en','UTC')")
    con.execute("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
    con.execute("INSERT INTO series(id,title,audio,licensed) VALUES ('s','S','nonverbal',0)")
    _insert(con, "one")
    _insert(con, "two", "analyzed")
    con.execute("INSERT INTO scenes(episode_id,start_ms,end_ms) VALUES (2,0,500)")
    con.commit()
    con.close()


def test_migration_0004_adds_split_columns_and_keeps_rows(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db, "0003")
    _seed(db)
    upgrade(db, "0004")
    assert current_revision(db) == "0004"
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    columns = {row[1]: row[2] for row in con.execute("PRAGMA table_info(episodes)")}
    for name in ("parent_episode_id", "span_start_ms", "span_end_ms"):
        assert columns[name] == "INTEGER"
    assert columns["logo_corner"] == "TEXT"
    assert dict(con.execute("SELECT sha256, status FROM episodes")) == {
        "one": "imported",
        "two": "analyzed",
    }
    assert con.execute("SELECT count(*) FROM scenes WHERE episode_id=2").fetchone() == (1,)
    # AUTOINCREMENT survives the table rebuild: a deleted id is never reused
    con.execute("DELETE FROM scenes")
    con.execute("DELETE FROM episodes WHERE sha256='two'")
    _insert(con, "three")
    assert con.execute("SELECT id FROM episodes WHERE sha256='three'").fetchone() == (3,)
    con.close()


def test_migration_0004_constraints(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db, "0003")
    _seed(db)
    upgrade(db, "0004")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    _insert(con, "parent:1", "split")  # the new status
    _insert(con, "child", parent_episode_id=1, span_start_ms=0, span_end_ms=500, logo_corner="tr")
    for corner in ("tl", "tr", "bl", "br"):
        _insert(con, f"logo-{corner}", logo_corner=corner)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "bad-corner", logo_corner="center")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "bad-status", "weird")
    with pytest.raises(sqlite3.IntegrityError):  # a child needs a real parent
        _insert(con, "orphan", parent_episode_id=999)
    with pytest.raises(sqlite3.IntegrityError):  # the old checks are still there
        con.execute("UPDATE episodes SET provenance='nope' WHERE sha256='one'")
    con.close()


def test_r16_migration_0004_again_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db, "0004")
    upgrade(db, "0004")
    assert current_revision(db) == "0004"
