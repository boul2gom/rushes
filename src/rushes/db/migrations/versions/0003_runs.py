"""Runs: closed list of statuses and the exit code of the command.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = ("running", "ok", "partial", "failed")


def upgrade() -> None:
    values = ", ".join(repr(v) for v in RUN_STATUSES)
    # nothing wrote a free-form status yet; map any stray value so the new check cannot refuse rows
    op.execute(f"UPDATE runs SET status='failed' WHERE status NOT IN ({values})")
    with op.batch_alter_table("runs", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("exit_code", sa.Integer()))
        batch.create_check_constraint(op.f("ck_runs_status"), f"status IN ({values})")


def downgrade() -> None:
    with op.batch_alter_table("runs", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint(op.f("ck_runs_status"), type_="check")
        batch.drop_column("exit_code")
