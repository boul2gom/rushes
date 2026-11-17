"""YouTube discovery: source video details, signals, channels; closed statuses.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIDEO_STATUSES = ("found", "retained", "downloaded", "rejected", "failed")
DISCOVERED_VIA = ("popular", "search", "channel", "rss", "backfill")
COMPILATION_STATUSES = ("proposed", "approved", "needs_review", "rejected")
SIGNAL_KINDS = ("heatmap", "comment_ts", "sponsor_highlight", "laughter", "quote", "community")
CHANNEL_STATUSES = ("candidate", "followed", "authorized", "rejected")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ck(table: str, name: str, expr: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expr, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    # the statuses were free text: map any stray value so the new checks cannot refuse a row
    op.execute(
        f"UPDATE source_videos SET status='found' WHERE status NOT IN "
        f"({', '.join(repr(v) for v in VIDEO_STATUSES)})"
    )
    op.execute(
        f"UPDATE compilations SET status='proposed' WHERE status NOT IN "
        f"({', '.join(repr(v) for v in COMPILATION_STATUSES)})"
    )
    with op.batch_alter_table(
        "source_videos", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("title", sa.Text()))
        batch.add_column(sa.Column("channel_handle", sa.Text()))
        batch.add_column(sa.Column("discovered_via", sa.Text()))
        batch.add_column(sa.Column("extracted_at", sa.Text()))
        batch.create_check_constraint(
            op.f("ck_source_videos_discovered_via"), _in("discovered_via", DISCOVERED_VIA)
        )
        batch.create_check_constraint(
            op.f("ck_source_videos_status"), _in("status", VIDEO_STATUSES)
        )
    with op.batch_alter_table("compilations", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.create_check_constraint(
            op.f("ck_compilations_status"), _in("status", COMPILATION_STATUSES)
        )
    op.create_table(
        "video_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_video_id", sa.Integer(), sa.ForeignKey("source_videos.id"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("collected_at", sa.Text(), nullable=False),
        _ck("video_signals", "kind", _in("kind", SIGNAL_KINDS)),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_video_signals_source_video_id_kind", "video_signals", ["source_video_id", "kind"]
    )
    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Text(), sa.ForeignKey("creators.id")),
        sa.Column("platform_channel_id", sa.Text(), nullable=False),
        sa.Column("country", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("momentum_score", sa.Float()),
        sa.Column("discovered_via", sa.Text()),
        sa.UniqueConstraint("platform_channel_id", name=op.f("uq_channels_platform_channel_id")),
        _ck("channels", "status", _in("status", CHANNEL_STATUSES)),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("channels")
    op.drop_index("ix_video_signals_source_video_id_kind", table_name="video_signals")
    op.drop_table("video_signals")
    with op.batch_alter_table("compilations", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint(op.f("ck_compilations_status"), type_="check")
    with op.batch_alter_table(
        "source_videos", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_constraint(op.f("ck_source_videos_status"), type_="check")
        batch.drop_constraint(op.f("ck_source_videos_discovered_via"), type_="check")
        batch.drop_column("extracted_at")
        batch.drop_column("discovered_via")
        batch.drop_column("channel_handle")
        batch.drop_column("title")
