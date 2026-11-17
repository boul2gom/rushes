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
        "INSERT INTO source_videos(platform,url,kind,series_id,status) "
        "VALUES ('youtube','https://y/1','official','s','weird')"
    )
    con.execute(
        "INSERT INTO source_videos(platform,url,kind,series_id,status) "
        "VALUES ('youtube','https://y/2','official','s','downloaded')"
    )
    con.execute("INSERT INTO compilations(kind,account_id,status) VALUES ('bestof','a','odd')")
    con.execute("INSERT INTO compilations(kind,account_id,status) VALUES ('bestof','a','approved')")
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "rushes.db"
    upgrade(path, "0004")
    _seed(path)
    upgrade(path)
    return path


def _con(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def test_migration_0005_adds_columns_and_maps_stray_statuses(db: Path) -> None:
    assert current_revision(db) == "0005"
    con = _con(db)
    columns = {row[1]: row[2] for row in con.execute("PRAGMA table_info(source_videos)")}
    for name in ("title", "channel_handle", "discovered_via", "extracted_at"):
        assert columns[name] == "TEXT"
    assert dict(con.execute("SELECT url, status FROM source_videos")) == {
        "https://y/1": "found",
        "https://y/2": "downloaded",
    }
    assert sorted(r[0] for r in con.execute("SELECT status FROM compilations")) == [
        "approved",
        "proposed",
    ]
    # AUTOINCREMENT survives the table rebuild
    con.execute("DELETE FROM source_videos WHERE url='https://y/2'")
    con.execute(
        "INSERT INTO source_videos(platform,url,kind,status) "
        "VALUES ('youtube','https://y/3','community','found')"
    )
    assert con.execute("SELECT id FROM source_videos WHERE url='https://y/3'").fetchone() == (3,)
    con.close()


def test_migration_0005_constraints(db: Path) -> None:
    con = _con(db)
    insert = (
        "INSERT INTO source_videos(platform,url,kind,status,discovered_via) "
        "VALUES ('youtube',?,'official',?,?)"
    )
    for status in ("found", "retained", "downloaded", "rejected", "failed"):
        con.execute(insert, (f"https://y/{status}", status, "popular"))
    for via in ("popular", "search", "channel", "rss", "backfill", None):
        con.execute(insert, (f"https://y/via-{via}", "found", via))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(insert, ("https://y/bad-status", "weird", "popular"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(insert, ("https://y/bad-via", "found", "tv"))
    with pytest.raises(sqlite3.IntegrityError):  # the old checks are still there
        con.execute("UPDATE source_videos SET kind='nope' WHERE url='https://y/1'")
    for status in ("proposed", "approved", "needs_review", "rejected"):
        con.execute(
            "INSERT INTO compilations(kind,account_id,status) VALUES ('bestof','a',?)", (status,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO compilations(kind,account_id,status) VALUES ('bestof','a','done')")
    con.close()


def test_migration_0005_video_signals(db: Path) -> None:
    con = _con(db)
    sql = (
        "INSERT INTO video_signals(source_video_id,kind,t_ms,weight,collected_at) "
        "VALUES (?,?,1000,0.5,'2026-11-16T12:00:00+00:00')"
    )
    for kind in ("heatmap", "comment_ts", "sponsor_highlight", "laughter", "quote", "community"):
        con.execute(sql, (1, kind))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(sql, (1, "weird"))
    with pytest.raises(sqlite3.IntegrityError):  # the video must exist
        con.execute(sql, (999, "heatmap"))
    indexes = {row[1] for row in con.execute("PRAGMA index_list(video_signals)")}
    assert "ix_video_signals_source_video_id_kind" in indexes
    con.close()


def test_migration_0005_channels(db: Path) -> None:
    con = _con(db)
    columns = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
    assert columns == {
        "id",
        "creator_id",
        "platform_channel_id",
        "country",
        "status",
        "momentum_score",
        "discovered_via",
    }
    sql = "INSERT INTO channels(platform_channel_id,status) VALUES (?,?)"
    for number, status in enumerate(("candidate", "followed", "authorized", "rejected")):
        con.execute(sql, (f"UC{number}", status))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(sql, ("UCx", "weird"))
    with pytest.raises(sqlite3.IntegrityError):  # one row per YouTube channel
        con.execute(sql, ("UC0", "candidate"))
    con.close()


def test_r16_migration_0005_again_changes_nothing(db: Path) -> None:
    upgrade(db)
    assert current_revision(db) == "0005"
