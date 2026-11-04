"""Config v2: no publication tool, originality, layouts, rights, derived values.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ck(table: str, name: str, expr: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expr, name=op.f(f"ck_{table}_{name}"))


ORIGINALITIES = ("base", "advanced")
LAYOUTS = ("square", "vertical", "portrait", "full")
ORIGINS = ("config", "prospect")
SUBJECT_STATUSES = ("candidate", "active", "dropped")
OLD_POST_STATUSES = (
    "planned",
    "rendered",
    "ready",
    "drafted",
    "scheduled",
    "published",
    "discarded",
    "expired",
)
NEW_POST_STATUSES = ("planned", "rendered", "ready", "published", "discarded", "expired")


def _subject_columns(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("origin", sa.Text(), nullable=False, server_default="config"))
        batch.add_column(sa.Column("status", sa.Text(), nullable=False, server_default="active"))
        batch.create_check_constraint(op.f(f"ck_{table}_origin"), _in("origin", ORIGINS))
        batch.create_check_constraint(op.f(f"ck_{table}_status"), _in("status", SUBJECT_STATUSES))


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint(op.f("ck_accounts_publish_mode"), type_="check")
        batch.drop_column("publish_mode")
        batch.drop_column("postiz_integration")
        batch.add_column(sa.Column("originality", sa.Text()))
        batch.create_check_constraint(
            op.f("ck_accounts_originality"), _in("originality", ORIGINALITIES)
        )

    # drafted/scheduled only existed for the abandoned tool: both mean "ready" now
    op.execute("UPDATE posts SET status='ready' WHERE status IN ('drafted','scheduled')")
    with op.batch_alter_table("posts", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint(op.f("ck_posts_status"), type_="check")
        batch.drop_column("postiz_post_id")
        batch.drop_column("postiz_status")
        batch.drop_column("postiz_synced_at")
        batch.add_column(sa.Column("layout", sa.Text()))
        batch.add_column(sa.Column("originality", sa.Text()))
        batch.create_check_constraint(op.f("ck_posts_status"), _in("status", NEW_POST_STATUSES))
        batch.create_check_constraint(op.f("ck_posts_layout"), _in("layout", LAYOUTS))
        batch.create_check_constraint(
            op.f("ck_posts_originality"), _in("originality", ORIGINALITIES)
        )

    _subject_columns("series")
    _subject_columns("creators")

    op.create_table(
        "rights",
        sa.Column("subject_kind", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("holder", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.Text()),
        sa.Column("priority", sa.Float()),
        sa.Column("showcase_posts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.Text(), nullable=False),
        _ck("rights", "subject_kind", _in("subject_kind", ("series", "creator"))),
        _ck(
            "rights",
            "status",
            _in("status", ("to_request", "requested", "granted", "refused")),
        ),
    )
    op.create_table(
        "derived_values",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "derived_values_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    raise NotImplementedError("0002 is one-way: the abandoned tool's data is not restorable")
