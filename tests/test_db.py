import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from helpers import make_episode, make_segment
from rushes.config import load_config
from rushes.context import Context
from rushes.db.engine import WRITE_LOCK, make_engine, write_engine, write_session
from rushes.db.migrate import current_revision, upgrade
from rushes.db.models import Assignment, Base, Episode
from rushes.db.registry import assign, can_assign
from rushes.db.sync import sync_config


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db = tmp_path / "rushes.db"
    upgrade(db)
    engine = make_engine(db)
    yield engine
    engine.dispose()


def _seed(engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(text("INSERT INTO brands VALUES ('b','cartoons','en','UTC')"))
        c.execute(
            text("INSERT INTO accounts(id,brand_id,platform,handle) VALUES ('a','b','tiktok','@a')")
        )
        c.execute(
            text("INSERT INTO series(id,title,audio,licensed) VALUES ('s','S','nonverbal',0)")
        )
        c.execute(
            text("INSERT INTO creators(id,language,channel_id,authorized) VALUES ('c','fr','UC',0)")
        )


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


def test_orm_models_match_migration(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_sync_config_is_idempotent(engine: Engine, config_dir: Path) -> None:
    config = load_config(config_dir)
    sync_config(engine, config)
    sync_config(engine, config)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM brands")).scalar() == 4
        assert c.execute(text("SELECT count(*) FROM accounts")).scalar() == 8
        assert c.execute(text("SELECT count(*) FROM series")).scalar() == 2
        assert c.execute(text("SELECT count(*) FROM creators")).scalar() == 5
        assert c.execute(text("SELECT count(*) FROM rights")).scalar() == 7


def test_sync_config_never_deletes_and_warns(
    engine: Engine, config_dir: Path, edit_yaml: Callable[[Path, Callable[[Any], Any]], None]
) -> None:
    sync_config(engine, load_config(config_dir))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO episodes(series_id,provenance,owner_account_id,file_path,sha256,"
                "duration_ms,status) VALUES ('oggy','local','st_tt','p.mp4','h',1000,'imported')"
            )
        )
    (config_dir / "series" / "oggy.yaml").unlink()
    edit_yaml(config_dir / "accounts.yaml", lambda d: d.pop("rotation"))  # oggy was rotated
    warnings = sync_config(engine, load_config(config_dir))
    assert any("oggy" in w for w in warnings)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM series WHERE id='oggy'")).scalar() == 1


def test_sync_rights_status_follows_config(
    engine: Engine, config_dir: Path, edit_yaml: Callable[[Path, Callable[[Any], Any]], None]
) -> None:
    """The config is the single source of truth for rights.status; priority/showcase are not."""
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(licensed=True))
    sync_config(engine, load_config(config_dir))

    def hand_edit(sql: str) -> None:
        with engine.begin() as c:
            c.execute(text(sql))

    def read(subject_id: str) -> tuple[str, float | None, int]:
        with engine.connect() as c:
            row = c.execute(
                text("SELECT status, priority, showcase_posts FROM rights WHERE subject_id=:i"),
                {"i": subject_id},
            ).one()
        return row[0], row[1], row[2]

    # no rights block: a status written only in the DB is overwritten ('granted' since licensed)
    hand_edit("UPDATE rights SET status='requested' WHERE subject_id='oggy'")
    hand_edit("UPDATE rights SET priority=0.8, showcase_posts=3 WHERE subject_id='oggy'")
    sync_config(engine, load_config(config_dir))
    assert read("oggy") == ("granted", 0.8, 3)

    # same for an unlicensed subject without a block: back to 'to_request'
    hand_edit("UPDATE rights SET status='requested' WHERE subject_id='byilhan'")
    sync_config(engine, load_config(config_dir))
    assert read("byilhan")[0] == "to_request"

    # a rights block in the config wins and stays
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d["rights"].update(status="requested"),
    )
    hand_edit("UPDATE rights SET priority=0.5, showcase_posts=2 WHERE subject_id='tom_and_jerry'")
    sync_config(engine, load_config(config_dir))
    sync_config(engine, load_config(config_dir))
    assert read("tom_and_jerry") == ("requested", 0.5, 2)


def test_sqlite_module_is_available() -> None:
    assert sqlite3.sqlite_version_info >= (3, 35)


def _default_isolation() -> str | None:
    with closing(sqlite3.connect(":memory:")) as probe:
        return probe.isolation_level


def _contender(ctx: Context) -> sqlite3.Connection:
    """Another process: raw connection with a short busy timeout."""
    return sqlite3.connect(ctx.paths.db, timeout=0.1, isolation_level=None)


def _locked(other: sqlite3.Connection) -> bool:
    """True when `other` cannot take the write lock right now."""
    try:
        other.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        assert "locked" in str(error)
        return True
    other.execute("ROLLBACK")
    return False


def test_r1_attribution_check_and_insert_are_atomic(ctx: Context, wsession: Session) -> None:
    segment = make_segment(wsession, make_episode(wsession), "part", 0, 10_000)
    wsession.commit()
    # the check alone already holds the write lock: no one can slip an assignment in between
    verdict = can_assign(
        wsession, segment.id, "st_tt", tolerance_ms=2000, phash_max=10, phash_min_share=0.25
    )
    assert verdict.ok
    other = _contender(ctx)
    try:
        assert _locked(other)
        wsession.rollback()  # the lock goes away with the transaction
        assert not _locked(other)
    finally:
        other.close()


def test_r1_attribution_insert_blocks_other_writers_until_commit(
    ctx: Context, wsession: Session
) -> None:
    segment = make_segment(wsession, make_episode(wsession), "part", 0, 10_000)
    wsession.commit()
    at = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    assert assign(
        wsession, segment.id, "st_tt", at=at, tolerance_ms=2000, phash_max=10, phash_min_share=0.25
    ).ok
    other = _contender(ctx)
    try:
        assert _locked(other)
        wsession.commit()
        other.execute("BEGIN IMMEDIATE")
        assert other.execute("SELECT count(*) FROM assignments").fetchone() == (1,)
        other.execute("ROLLBACK")
    finally:
        other.close()


def test_r1_write_session_takes_the_lock_again_for_each_transaction(
    ctx: Context, wsession: Session
) -> None:
    wsession.execute(select(1))
    wsession.commit()
    other = _contender(ctx)
    try:
        assert not _locked(other)  # committed: lock released
        wsession.execute(select(1))  # next transaction
        assert _locked(other)
    finally:
        other.close()


def test_plain_sessions_never_block_on_a_writer_in_wal(ctx: Context, wsession: Session) -> None:
    segment = make_segment(wsession, make_episode(wsession), "part", 0, 10_000)
    wsession.commit()
    assert can_assign(
        wsession, segment.id, "st_tt", tolerance_ms=2000, phash_max=10, phash_min_share=0.25
    ).ok
    # wsession holds the write lock; readers on the default engine are not blocked (WAL)
    with Session(ctx.engine) as reader:
        assert reader.scalar(select(Assignment.segment_id)) is None
        assert reader.get(type(segment), segment.id) is not None
    with ctx.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM segments")).scalar() == 1


def test_default_engine_transaction_is_deferred(ctx: Context, session: Session) -> None:
    session.execute(select(1))  # a plain session reads without taking the write lock
    other = _contender(ctx)
    try:
        assert not _locked(other)
    finally:
        other.close()
    with ctx.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        other = _contender(ctx)
        try:
            assert not _locked(other)
        finally:
            other.close()


def test_write_lock_flag_follows_the_transaction(ctx: Context) -> None:
    with write_engine(ctx.engine).connect() as conn:
        conn.execute(text("SELECT 1"))
        assert conn.info[WRITE_LOCK] is True
        conn.commit()
        assert conn.info[WRITE_LOCK] is False
        conn.execute(text("SELECT 1"))
        assert conn.info[WRITE_LOCK] is True
        conn.rollback()
        assert conn.info[WRITE_LOCK] is False
    with ctx.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        assert conn.info[WRITE_LOCK] is False


def test_plain_engine_keeps_the_pysqlite_default_transaction_control(ctx: Context) -> None:
    default = _default_isolation()
    with ctx.engine.connect() as conn:
        assert conn.connection.dbapi_connection.isolation_level == default  # type: ignore[union-attr]
        conn.execute(text("SELECT 1"))
        conn.rollback()


def test_plain_read_then_write_survives_a_commit_in_between(ctx: Context) -> None:
    # task 2 behaviour: a SELECT opens no snapshot, so another process may commit meanwhile
    other = _contender(ctx)
    try:
        with ctx.engine.begin() as conn:
            conn.execute(text("SELECT count(*) FROM brands")).scalar()
            other.execute("INSERT INTO brands VALUES ('o','cartoons','en','UTC')")
            conn.execute(text("INSERT INTO brands VALUES ('p','cartoons','en','UTC')"))
    finally:
        other.close()
    with ctx.engine.connect() as conn:
        ids = set(conn.execute(text("SELECT id FROM brands")).scalars())
    assert {"o", "p"} <= ids


def test_plain_read_then_write_waits_for_a_lock_holder(ctx: Context) -> None:
    holder = sqlite3.connect(ctx.paths.db, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, lambda: holder.execute("COMMIT"))
    try:
        with ctx.engine.begin() as conn:
            conn.execute(text("SELECT count(*) FROM brands")).scalar()
            release.start()
            # waits on the busy handler (5 s) instead of failing at once
            conn.execute(text("INSERT INTO brands VALUES ('w','cartoons','en','UTC')"))
    finally:
        release.join()
        holder.close()
    with ctx.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM brands WHERE id='w'")).scalar() == 1


def test_write_session_does_not_retake_the_lock_after_commit(
    ctx: Context, wsession: Session
) -> None:
    episode = make_episode(wsession)
    wsession.commit()
    assert episode.id is not None  # attributes stay loaded: no refresh, no new transaction
    assert wsession.get(Episode, episode.id) is episode
    other = _contender(ctx)
    try:
        assert not _locked(other)
    finally:
        other.close()


def test_isolation_level_returns_to_default_after_a_write_transaction(ctx: Context) -> None:
    default = _default_isolation()
    for finish in ("commit", "rollback"):
        with write_engine(ctx.engine).connect() as conn:
            conn.execute(text("SELECT 1"))
            assert conn.connection.dbapi_connection.isolation_level is None  # type: ignore[union-attr]
            getattr(conn, finish)()
            assert conn.connection.dbapi_connection.isolation_level == default  # type: ignore[union-attr]


def test_pooled_connection_carries_no_stale_write_state(ctx: Context) -> None:
    default = _default_isolation()
    leaked = write_engine(ctx.engine).connect()
    leaked.execute(text("SELECT 1"))
    assert leaked.info[WRITE_LOCK] is True
    leaked.connection.close()  # back to the pool without any commit/rollback event
    leaked.close()
    with ctx.engine.connect() as conn:
        assert conn.info.get(WRITE_LOCK) is not True
        assert conn.connection.dbapi_connection.isolation_level == default  # type: ignore[union-attr]
        conn.execute(text("SELECT 1"))
        assert conn.info[WRITE_LOCK] is False
    other = _contender(ctx)
    try:
        assert not _locked(other)
    finally:
        other.close()


@pytest.mark.parametrize("kind", ["plain", "write"])
def test_interrupt_inside_a_statement_propagates_unchanged(ctx: Context, kind: str) -> None:
    # SQLAlchemy invalidates the connection on KeyboardInterrupt; our listeners must not mask it
    def interrupt(cursor: Any, statement: str, parameters: Any, context: Any) -> None:
        if statement.startswith("SELECT 2"):
            raise KeyboardInterrupt

    event.listen(ctx.engine, "do_execute", interrupt)
    try:
        db = Session(ctx.engine) if kind == "plain" else write_session(ctx.engine)
        with pytest.raises(KeyboardInterrupt), db:
            db.execute(text("SELECT 1"))
            db.execute(text("SELECT 2"))
    finally:
        event.remove(ctx.engine, "do_execute", interrupt)
    with ctx.engine.connect() as conn:  # the engine is still usable, back to the plain default
        assert conn.execute(text("SELECT 3")).scalar() == 3
        assert conn.connection.dbapi_connection.isolation_level == _default_isolation()  # type: ignore[union-attr]
        assert conn.info.get(WRITE_LOCK) is not True
    other = _contender(ctx)
    try:
        assert not _locked(other)
    finally:
        other.close()


def test_invalidating_a_plain_connection_does_not_raise(ctx: Context) -> None:
    conn = ctx.engine.connect()
    conn.execute(text("SELECT 1"))
    conn.invalidate()
    conn.close()
    with ctx.engine.connect() as again:
        assert again.execute(text("SELECT 1")).scalar() == 1
