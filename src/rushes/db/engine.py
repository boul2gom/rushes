"""SQLite engine with the pragmas the project relies on.

A plain engine is pysqlite's own, untouched: no BEGIN before the first write, so a transaction
that reads then writes waits on the busy handler (`busy_timeout`) instead of failing, and WAL
readers never wait for a writer.

Attribution (`db.registry.assign`, R1) needs the check and the insert under one write lock: it runs
in a `write_session(engine)`, whose transactions open with `BEGIN IMMEDIATE`. For their duration
only, the driver is switched to `isolation_level = None` (explicit BEGIN); it goes back to the
default at commit, rollback or pool check-in. Nothing else takes the write lock early.

Rule for every other task: never keep a transaction (or a Session) open during slow work or a
heavy-lock handover; read, close, work, then write in a short transaction.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session

BEGIN_OPTION = "rushes_begin"  # execution option: "IMMEDIATE" to open the write lock up front
WRITE_LOCK = "rushes_write_lock"  # Connection.info key: the current transaction holds the lock
_LEGACY = ""  # pysqlite's default isolation_level (implicit, deferred BEGIN before DML)


def _reset(dbapi_connection: Any, info: Any) -> None:
    dbapi_connection.isolation_level = _LEGACY
    info[WRITE_LOCK] = False


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        connection.info[WRITE_LOCK] = False  # never trust a value left by an earlier transaction
        if connection.get_execution_options().get(BEGIN_OPTION) == "IMMEDIATE":
            dbapi = connection.connection.dbapi_connection
            assert dbapi is not None
            dbapi.isolation_level = None  # the driver stops managing this transaction
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.info[WRITE_LOCK] = True

    @event.listens_for(engine, "commit")
    @event.listens_for(engine, "rollback")
    def _release(connection: Connection) -> None:
        if connection.invalidated:  # e.g. KeyboardInterrupt in a statement: check-in cleans up
            return
        if connection.info.get(WRITE_LOCK):
            dbapi = connection.connection.dbapi_connection
            assert dbapi is not None
            _reset(dbapi, connection.info)

    @event.listens_for(engine.pool, "checkin")
    def _checkin(dbapi_connection: Any, record: Any) -> None:
        # the pool already rolled back: forget any write transaction (None when invalidated)
        if dbapi_connection is not None:
            _reset(dbapi_connection, record.info)
        else:
            record.info[WRITE_LOCK] = False

    return engine


def write_engine(engine: Engine) -> Engine:
    """Same engine and pool, whose transactions open with BEGIN IMMEDIATE."""
    return engine.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})


def write_session(engine: Engine) -> Session:
    """Session for attribution: its first statement takes the write lock (R1).

    `expire_on_commit=False`: reading an attribute after a commit must not start (and lock) a new
    transaction. Close the session as soon as the attribution is committed.
    """
    return Session(write_engine(engine), expire_on_commit=False)
