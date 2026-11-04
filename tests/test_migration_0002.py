import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from rushes.config import load_config
from rushes.db.engine import make_engine
from rushes.db.migrate import current_revision, upgrade
from rushes.db.sync import sync_config


def _columns(db: Path, table: str) -> set[str]:
    engine = make_engine(db)
    with engine.connect() as c:
        rows = c.execute(text(f"PRAGMA table_info({table})")).all()
    engine.dispose()
    return {r[1] for r in rows}


def _tables(db: Path) -> set[str]:
    engine = make_engine(db)
    with engine.connect() as c:
        rows = c.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars().all()
    engine.dispose()
    return set(rows)


def test_migration_0002_keeps_rows_and_maps_statuses(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db, "0001")
    assert current_revision(db) == "0001"
    engine = make_engine(db)
    with engine.begin() as c:
        c.execute(text("INSERT INTO brands VALUES ('b','cartoons','en','UTC')"))
        c.execute(
            text(
                "INSERT INTO accounts(id,brand_id,platform,handle,publish_mode) "
                "VALUES ('a','b','tiktok','@a','manual')"
            )
        )
        c.execute(text("INSERT INTO series VALUES ('s','S','nonverbal',0)"))
        c.execute(text("INSERT INTO creators VALUES ('c','fr','UC',0)"))
        c.execute(
            text(
                "INSERT INTO episodes(series_id,provenance,owner_account_id,file_path,sha256,"
                "duration_ms,status) VALUES ('s','local','a','p.mp4','h',1000,'imported')"
            )
        )
        c.execute(text("INSERT INTO posts(account_id,status) VALUES ('a','drafted')"))
        c.execute(text("INSERT INTO posts(account_id,status) VALUES ('a','scheduled')"))
        c.execute(text("INSERT INTO posts(account_id,status) VALUES ('a','published')"))
    engine.dispose()

    upgrade(db)
    assert current_revision(db) == "0002"

    engine = make_engine(db)
    with engine.connect() as c:
        accounts = c.execute(text("SELECT id, brand_id, originality FROM accounts")).all()
        statuses = c.execute(text("SELECT status FROM posts ORDER BY id")).scalars().all()
        series = c.execute(text("SELECT origin, status FROM series")).one()
        creators = c.execute(text("SELECT origin, status FROM creators")).one()
        episodes = c.execute(text("SELECT count(*) FROM episodes")).scalar()
        assert c.execute(text("PRAGMA foreign_key_check")).all() == []
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        layout_originality = c.execute(text("SELECT layout, originality FROM posts")).all()
    engine.dispose()

    assert accounts == [("a", "b", None)]
    assert statuses == ["ready", "ready", "published"]
    assert tuple(series) == ("config", "active")
    assert tuple(creators) == ("config", "active")
    assert episodes == 1
    assert all(tuple(r) == (None, None) for r in layout_originality)

    acc_cols = _columns(db, "accounts")
    assert "originality" in acc_cols
    assert not {"publish_mode", "postiz_integration"} & acc_cols
    post_cols = _columns(db, "posts")
    assert {"layout", "originality"} <= post_cols
    assert not {"postiz_post_id", "postiz_status", "postiz_synced_at"} & post_cols
    assert {"rights", "derived_values", "derived_values_history"} <= _tables(db)


def test_migration_0002_new_constraints(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db)
    engine = make_engine(db)
    with engine.begin() as c:
        c.execute(text("INSERT INTO brands VALUES ('b','cartoons','en','UTC')"))
        c.execute(
            text("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
        )
    for sql in (
        "INSERT INTO posts(account_id,status) VALUES ('a','drafted')",
        "INSERT INTO posts(account_id,status,layout) VALUES ('a','planned','weird')",
        "INSERT INTO posts(account_id,status,originality) VALUES ('a','planned','weird')",
        "UPDATE accounts SET originality='weird'",
        "INSERT INTO series(id,title,audio,licensed,origin) VALUES ('x','X','nonverbal',0,'?')",
        "INSERT INTO series(id,title,audio,licensed,status) VALUES ('x','X','nonverbal',0,'?')",
    ):
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(sql))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO posts(account_id,status,layout,originality) "
                "VALUES ('a','ready','vertical','advanced')"
            )
        )
        c.execute(
            text(
                "INSERT INTO derived_values_history(key,scope,value_json,source,confidence,"
                "computed_at) VALUES ('k','s','1','t',1.0,'2026-11-01')"
            )
        )
        c.execute(
            text(
                "INSERT INTO derived_values_history(key,scope,value_json,source,confidence,"
                "computed_at) VALUES ('k','s','2','t',1.0,'2026-11-02')"
            )
        )
        ids = c.execute(text("SELECT id FROM derived_values_history ORDER BY id")).scalars().all()
        assert ids == [1, 2]
    engine.dispose()


def test_migration_0002_keeps_autoincrement_on_posts(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db)
    engine = make_engine(db)
    with engine.connect() as c:
        sql = c.execute(text("SELECT sql FROM sqlite_master WHERE name='posts'")).scalar()
    engine.dispose()
    assert sql is not None
    assert "AUTOINCREMENT" in sql


def test_sync_writes_origin_status_and_rights(tmp_path: Path, config_dir: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db)
    engine = make_engine(db)
    sync_config(engine, load_config(config_dir))
    with engine.connect() as c:
        tj = c.execute(text("SELECT origin, status FROM series WHERE id='tom_and_jerry'")).one()
        right = c.execute(
            text(
                "SELECT status, holder FROM rights "
                "WHERE subject_kind='series' AND subject_id='tom_and_jerry'"
            )
        ).one()
        oggy = c.execute(text("SELECT status FROM rights WHERE subject_id='oggy'")).scalar()
        creator = c.execute(text("SELECT origin, status FROM creators WHERE id='squeezie'")).one()
        creator_right = c.execute(
            text("SELECT status FROM rights WHERE subject_kind='creator' AND subject_id='squeezie'")
        ).scalar()
        originality = dict(c.execute(text("SELECT id, originality FROM accounts")).all())
    engine.dispose()
    assert tuple(tj) == ("config", "active")
    assert tuple(creator) == ("config", "active")
    assert tuple(right) == ("to_request", "Warner Bros. Discovery")
    assert oggy == "to_request"
    assert creator_right == "to_request"
    assert originality["tt_tt"] == "advanced"
    assert originality["st_tt"] is None


def test_sync_licensed_series_without_rights_block_is_granted(
    tmp_path: Path,
    config_dir: Path,
    edit_yaml: Callable[[Path, Callable[[Any], Any]], None],
) -> None:
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(licensed=True))
    edit_yaml(config_dir / "creators" / "byilhan.yaml", lambda d: d.update(authorized=True))
    db = tmp_path / "r.db"
    upgrade(db)
    engine = make_engine(db)
    sync_config(engine, load_config(config_dir))
    with engine.connect() as c:
        assert (
            c.execute(text("SELECT status FROM rights WHERE subject_id='oggy'")).scalar()
            == "granted"
        )
        assert (
            c.execute(text("SELECT status FROM rights WHERE subject_id='byilhan'")).scalar()
            == "granted"
        )
    engine.dispose()


def test_sync_never_overwrites_priority_or_showcase_posts(tmp_path: Path, config_dir: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db)
    engine = make_engine(db)
    config = load_config(config_dir)
    sync_config(engine, config)
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE rights SET priority=0.8, showcase_posts=3 "
                "WHERE subject_kind='series' AND subject_id='oggy'"
            )
        )
    sync_config(engine, config)
    with engine.connect() as c:
        row = c.execute(
            text("SELECT priority, showcase_posts FROM rights WHERE subject_id='oggy'")
        ).one()
    engine.dispose()
    assert tuple(row) == (0.8, 3)


def test_migration_failure_rolls_back_ddl(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db, "0001")
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("INSERT INTO brands VALUES ('b','cartoons','en','UTC')")
    raw.execute("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
    raw.execute("INSERT INTO posts(account_id,status) VALUES ('a','drafted')")
    raw.execute("INSERT INTO posts(account_id,status) VALUES ('ghost','scheduled')")  # dangling FK
    raw.commit()
    raw.close()

    with pytest.raises(RuntimeError, match="clés étrangères"):
        upgrade(db)

    # nothing of 0002 survived: no leftover temp table, same revision, same columns and data
    assert current_revision(db) == "0001"
    tables = _tables(db)
    assert not {t for t in tables if t.startswith("_alembic_tmp")}
    assert not {"rights", "derived_values", "derived_values_history"} & tables
    assert {"publish_mode", "postiz_integration"} <= _columns(db, "accounts")
    assert {"postiz_status", "postiz_post_id"} <= _columns(db, "posts")
    check = sqlite3.connect(db)
    assert check.execute("SELECT status FROM posts ORDER BY id").fetchall() == [
        ("drafted",),
        ("scheduled",),
    ]
    # fix the data: the retry succeeds
    check.execute(
        "INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('ghost','b','instagram','@g')"
    )
    check.commit()
    check.close()
    upgrade(db)
    assert current_revision(db) == "0002"
    final = sqlite3.connect(db)
    assert final.execute("SELECT status FROM posts ORDER BY id").fetchall() == [
        ("ready",),
        ("ready",),
    ]
    final.close()


def test_upgrade_on_current_db_is_a_noop_even_with_existing_violations(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    upgrade(db)
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("INSERT INTO posts(account_id,status) VALUES ('ghost','planned')")  # dangling FK
    raw.commit()
    raw.close()

    upgrade(db)  # nothing pending: no foreign-key audit, no failure

    assert current_revision(db) == "0002"
    check = sqlite3.connect(db)
    assert check.execute("SELECT account_id, status FROM posts").fetchall() == [
        ("ghost", "planned")
    ]
    check.close()
