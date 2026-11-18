import sqlite3
from pathlib import Path

import pytest

from rushes.db.migrate import current_revision, upgrade


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    con.execute("INSERT INTO brands VALUES ('b','cartoons','en','UTC')")
    con.execute("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
    con.execute("INSERT INTO series(id,title,audio,licensed) VALUES ('s','S','nonverbal',0)")
    con.execute(
        "INSERT INTO episodes(series_id,provenance,owner_account_id,file_path,sha256,duration_ms,"
        "status) VALUES ('s','local','a','e.mp4','h',1000,'analyzed')"
    )
    con.execute("INSERT INTO scenes(episode_id,start_ms,end_ms) VALUES (1,0,500),(1,500,1000)")
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "rushes.db"
    upgrade(path, "0005")
    _seed(path)
    upgrade(path, "0006")  # pinned: later revisions have their own tests
    return path


def _con(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def test_migration_0006_creates_scene_vectors_and_words(db: Path) -> None:
    assert current_revision(db) == "0006"
    con = _con(db)
    vectors = {
        row[1]: (row[2], row[3], row[5]) for row in con.execute("PRAGMA table_info(scene_vectors)")
    }
    assert vectors == {
        "scene_id": ("INTEGER", 1, 1),  # (type, not null, primary key)
        "model": ("TEXT", 1, 0),
        "dim": ("INTEGER", 1, 0),
        "embedding": ("BLOB", 1, 0),
    }
    words = {row[1]: row[2] for row in con.execute("PRAGMA table_info(words)")}
    assert words == {
        "id": "INTEGER",
        "episode_id": "INTEGER",
        "start_ms": "INTEGER",
        "end_ms": "INTEGER",
        "word": "TEXT",
        "prob": "FLOAT",
        "sentence_id": "INTEGER",
    }
    indexes = {row[1]: row[2] for row in con.execute("PRAGMA index_list(words)")}
    assert "ix_words_episode_id_start_ms" in indexes
    columns = [row[2] for row in con.execute("PRAGMA index_info(ix_words_episode_id_start_ms)")]
    assert columns == ["episode_id", "start_ms"]
    con.close()


def test_migration_0006_vectors_follow_their_scene(db: Path) -> None:
    con = _con(db)
    insert = (
        "INSERT INTO scene_vectors(scene_id,model,dim,embedding) "
        "VALUES (?,'m',2,x'0000803f00000000')"
    )
    con.execute(insert, (1,))
    con.execute(insert, (2,))
    with pytest.raises(sqlite3.IntegrityError):  # one vector per scene
        con.execute(insert, (1,))
    with pytest.raises(sqlite3.IntegrityError):  # the scene must exist
        con.execute(insert, (99,))
    con.execute("DELETE FROM scenes WHERE id=1")  # ON DELETE CASCADE (analyze --force)
    assert con.execute("SELECT scene_id FROM scene_vectors").fetchall() == [(2,)]
    con.close()


def test_migration_0006_words_belong_to_an_episode(db: Path) -> None:
    con = _con(db)
    sql = (
        "INSERT INTO words(episode_id,start_ms,end_ms,word,prob,sentence_id) "
        "VALUES (?,0,300,'hi',0.9,0)"
    )
    con.execute(sql, (1,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(sql, (99,))
    con.close()


def test_r16_migration_0006_again_changes_nothing(db: Path) -> None:
    before = _con(db).execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
    upgrade(db, "0006")
    assert _con(db).execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == before
