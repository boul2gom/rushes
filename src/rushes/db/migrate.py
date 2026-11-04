"""Versioned migrations (Alembic, programmatic API, no alembic.ini)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext

from rushes.db.engine import make_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def upgrade(db_path: Path) -> None:
    """Apply every pending migration; running it again changes nothing (R16)."""
    engine = make_engine(db_path)
    try:
        with engine.begin() as connection:
            cfg = Config()
            cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
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
