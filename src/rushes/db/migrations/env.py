"""Alembic environment, driven programmatically by rushes.db.migrate."""

from alembic import context
from sqlalchemy import Connection

from rushes.db.models import Base


def run_migrations() -> None:
    connection: Connection = context.config.attributes["connection"]
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
