"""Compilations split into child episodes, burned-in logo corner.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = ("imported", "cleaned", "analyzed")
NEW_STATUSES = ("imported", "split", "cleaned", "analyzed")
CORNERS = ("tl", "tr", "bl", "br")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("episodes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("parent_episode_id", sa.Integer()))
        batch.add_column(sa.Column("span_start_ms", sa.Integer()))
        batch.add_column(sa.Column("span_end_ms", sa.Integer()))
        batch.add_column(sa.Column("logo_corner", sa.Text()))
        batch.drop_constraint(op.f("ck_episodes_status"), type_="check")
        batch.create_check_constraint(op.f("ck_episodes_status"), _in("status", NEW_STATUSES))
        batch.create_check_constraint(op.f("ck_episodes_logo_corner"), _in("logo_corner", CORNERS))
        batch.create_foreign_key(
            op.f("fk_episodes_parent_episode_id_episodes"),
            "episodes",
            ["parent_episode_id"],
            ["id"],
        )


def downgrade() -> None:
    # a split parent and its children cannot exist in the old schema: the children go, the parent
    # is a plain imported episode again (its clean.* files are not rebuilt here)
    op.execute("DELETE FROM episodes WHERE parent_episode_id IS NOT NULL")
    op.execute("UPDATE episodes SET status='imported' WHERE status='split'")
    with op.batch_alter_table("episodes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint(op.f("fk_episodes_parent_episode_id_episodes"), type_="foreignkey")
        batch.drop_constraint(op.f("ck_episodes_logo_corner"), type_="check")
        batch.drop_constraint(op.f("ck_episodes_status"), type_="check")
        batch.create_check_constraint(op.f("ck_episodes_status"), _in("status", OLD_STATUSES))
        batch.drop_column("logo_corner")
        batch.drop_column("span_end_ms")
        batch.drop_column("span_start_ms")
        batch.drop_column("parent_episode_id")
