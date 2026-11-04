"""Versioned migrations (Alembic, programmatic API, no alembic.ini)."""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, event

from rushes.db.engine import make_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _migration_engine(db_path: Path) -> Engine:
    """Engine whose connections run the whole migration in ONE real transaction.

    pysqlite starts transactions lazily (only before DML), so DDL would run outside any
    transaction and survive a failure. The documented recipe: the driver stops managing
    transactions (`isolation_level = None`) and SQLAlchemy emits an explicit BEGIN. SQLite DDL is
    transactional, so a failing step (or the final foreign-key check) rolls everything back.
    """
    engine = make_engine(db_path)

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        # batch mode recreates tables other tables point to: keys off while it runs, checked at
        # the end. The pragma is a no-op inside a transaction, so it is set here, outside BEGIN.
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")

    @event.listens_for(engine, "begin")
    def _begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


def upgrade(db_path: Path, revision: str = "head") -> None:
    """Apply pending migrations up to `revision`, all or nothing; again changes nothing (R16).

    Guaranteed: if any migration step or the final foreign-key check fails, the database keeps
    its previous schema, data and revision (no leftover temporary table). The foreign-key check
    only runs when a migration was applied: an up-to-date database is a no-op.
    """
    engine = _migration_engine(db_path)
    try:
        with engine.connect() as connection, connection.begin():
            cfg = Config()
            cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
            cfg.attributes["connection"] = connection
            before = MigrationContext.configure(connection).get_current_revision()
            command.upgrade(cfg, revision)
            if MigrationContext.configure(connection).get_current_revision() != before:
                # only audit what a migration just rewrote; a current database is left alone
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"migration: clés étrangères violées : {broken}")
    finally:
        engine.dispose()


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    engine = make_engine(db_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
