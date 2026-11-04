import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from rushes.config import load_config
from rushes.db.engine import make_engine
from rushes.db.migrate import current_revision, upgrade
from rushes.db.models import Base
from rushes.db.sync import sync_config


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "rushes.db"
    upgrade(db)
    return make_engine(db)


def _seed(engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(text("INSERT INTO brands VALUES ('b','cartoons','en','UTC')"))
        c.execute(
            text("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
        )
        c.execute(text("INSERT INTO series VALUES ('s','S','nonverbal',0)"))
        c.execute(text("INSERT INTO creators VALUES ('c','fr','UC',0)"))


def _episode(sha: str = "x", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "series_id": "s",
        "creator_id": None,
        "provenance": "local",
        "owner": "a",
        "path": "p.mp4",
        "sha": sha,
        "dur": 1000,
        "status": "imported",
    }
    row.update(overrides)
    return row


_EPISODE_SQL = text(
    "INSERT INTO episodes(series_id,creator_id,provenance,owner_account_id,file_path,"
    "sha256,duration_ms,status) VALUES (:series_id,:creator_id,:provenance,:owner,:path,"
    ":sha,:dur,:status)"
)


def test_r16_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    assert current_revision(db) is None
    upgrade(db)
    first = current_revision(db)
    assert first is not None
    upgrade(db)
    assert current_revision(db) == first


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO accounts(id,brand_id,platform,handle) "
                "VALUES ('a','nope','tiktok','@a')"
            )
        )


def test_connection_pragmas(engine: Engine) -> None:
    with engine.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_publish_mode_defaults_to_manual_r48(engine: Engine) -> None:
    _seed(engine)
    with engine.connect() as c:
        mode = c.execute(text("SELECT publish_mode FROM accounts WHERE id='a'")).scalar()
    assert mode == "manual"


def test_r44_episode_requires_single_owner(engine: Engine) -> None:
    _seed(engine)
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode(owner=None))


def test_episode_needs_exactly_one_of_series_or_creator(engine: Engine) -> None:
    _seed(engine)
    for bad in ({"series_id": "s", "creator_id": "c"}, {"series_id": None, "creator_id": None}):
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(_EPISODE_SQL, _episode(sha="y", **bad))
    with engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode(sha="ok"))


def test_duplicate_sha256_is_refused(engine: Engine) -> None:
    _seed(engine)
    with engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode(sha="same"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode(sha="same"))


def test_segment_end_must_follow_start(engine: Engine) -> None:
    _seed(engine)
    with engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode())
    sql = text(
        "INSERT INTO segments(episode_id,kind,start_ms,end_ms,family,status) "
        "VALUES (1,'part',:s,:e,'monetizable','proposed')"
    )
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(sql, {"s": 5000, "e": 5000})
    with engine.begin() as c:
        c.execute(sql, {"s": 0, "e": 70000})


def test_status_outside_enumeration_is_refused(engine: Engine) -> None:
    _seed(engine)
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode(status="weird"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("INSERT INTO posts(account_id,status) VALUES ('a','test')"))


def test_posts_is_test_defaults_to_zero(engine: Engine) -> None:
    _seed(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO posts(account_id,status) VALUES ('a','planned')"))
    with engine.connect() as c:
        assert c.execute(text("SELECT is_test FROM posts")).scalar() == 0


def test_assignment_is_unique_per_segment(engine: Engine) -> None:
    _seed(engine)
    with engine.begin() as c:
        c.execute(_EPISODE_SQL, _episode())
        c.execute(
            text(
                "INSERT INTO segments(episode_id,kind,start_ms,end_ms,family,status) "
                "VALUES (1,'part',0,70000,'monetizable','approved')"
            )
        )
        c.execute(text("INSERT INTO assignments VALUES (1,'a','2026-11-01')"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("INSERT INTO assignments VALUES (1,'a','2026-11-02')"))


def test_orm_models_match_migration(tmp_path: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db)
    engine = make_engine(db)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_sync_config_is_idempotent(tmp_path: Path, config_dir: Path) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db)
    engine = make_engine(db)
    config = load_config(config_dir)
    sync_config(engine, config)
    sync_config(engine, config)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM brands")).scalar() == 4
        assert c.execute(text("SELECT count(*) FROM accounts")).scalar() == 8
        assert c.execute(text("SELECT count(*) FROM series")).scalar() == 3
        assert c.execute(text("SELECT count(*) FROM creators")).scalar() == 2


def test_sync_config_never_deletes_and_warns(
    tmp_path: Path, config_dir: Path, edit_yaml: object
) -> None:
    db = tmp_path / "rushes.db"
    upgrade(db)
    engine = make_engine(db)
    sync_config(engine, load_config(config_dir))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO episodes(series_id,provenance,owner_account_id,file_path,sha256,"
                "duration_ms,status) VALUES ('grizzy','local','st_tt','p.mp4','h',1000,'imported')"
            )
        )
    (config_dir / "series" / "grizzy.yaml").unlink()
    warnings = sync_config(engine, load_config(config_dir))
    assert any("grizzy" in w for w in warnings)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM series WHERE id='grizzy'")).scalar() == 1


def test_sqlite_module_is_available() -> None:
    assert sqlite3.sqlite_version_info >= (3, 35)
