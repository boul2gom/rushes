"""Initial schema: core of the pipeline (phases 1 and 2).

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ck(table: str, name: str, expr: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expr, name=op.f(f"ck_{table}_{name}"))


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("vertical", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        _ck("brands", "vertical", _in("vertical", ("cartoons", "creators"))),
    )
    op.create_table(
        "series",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("audio", sa.Text(), nullable=False),
        sa.Column("licensed", sa.Boolean(), nullable=False),
        _ck("series", "audio", _in("audio", ("nonverbal", "dialogue_en"))),
    )
    op.create_table(
        "creators",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("authorized", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("brand_id", sa.Text(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("handle", sa.Text(), nullable=False),
        sa.Column("publish_mode", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("postiz_integration", sa.Text()),
        sa.UniqueConstraint("brand_id", "platform", name="uq_accounts_brand_id_platform"),
        _ck("accounts", "platform", _in("platform", ("tiktok", "instagram"))),
        _ck("accounts", "publish_mode", _in("publish_mode", ("manual", "postiz"))),
    )
    op.create_table(
        "source_videos",
        _id(),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text()),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("series_id", sa.Text(), sa.ForeignKey("series.id")),
        sa.Column("creator_id", sa.Text(), sa.ForeignKey("creators.id")),
        sa.Column("views", sa.Integer()),
        sa.Column("likes", sa.Integer()),
        sa.Column("comments", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("published_at", sa.Text()),
        sa.Column("heatmap_json", sa.Text()),
        sa.Column("sponsorblock_json", sa.Text()),
        sa.Column("score", sa.Float()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.UniqueConstraint("url", name=op.f("uq_source_videos_url")),
        _ck("source_videos", "kind", _in("kind", ("official", "community"))),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "episodes",
        _id(),
        sa.Column("series_id", sa.Text(), sa.ForeignKey("series.id")),
        sa.Column("creator_id", sa.Text(), sa.ForeignKey("creators.id")),
        sa.Column("source_video_id", sa.Integer(), sa.ForeignKey("source_videos.id")),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("owner_account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.UniqueConstraint("sha256", name=op.f("uq_episodes_sha256")),
        _ck("episodes", "provenance", _in("provenance", ("official", "community", "local"))),
        _ck("episodes", "status", _in("status", ("imported", "cleaned", "analyzed"))),
        _ck("episodes", "one_origin", "(series_id IS NULL) <> (creator_id IS NULL)"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "scenes",
        _id(),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("loudness", sa.Float()),
        sa.Column("motion", sa.Float()),
        sa.Column("speech", sa.Float()),
        sa.Column("tags_json", sa.Text()),
        sa.Column("characters_json", sa.Text()),
        sa.Column("sound_events_json", sa.Text()),
        sa.Column("popularity", sa.Float()),
        _ck("scenes", "span", "end_ms > start_ms"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "segments",
        _id(),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("target_ms", sa.Integer()),
        sa.Column("seed", sa.Integer()),
        sa.Column("signal_level", sa.Integer()),
        sa.Column("score", sa.Float()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("phash_json", sa.Text()),
        _ck("segments", "kind", _in("kind", ("part", "bestof_item", "compilation_item"))),
        _ck(
            "segments",
            "status",
            _in("status", ("proposed", "approved", "needs_review", "rejected")),
        ),
        _ck("segments", "span", "end_ms > start_ms"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "assignments",
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id"), primary_key=True),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("assigned_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "compilations",
        _id(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id")),
        sa.Column("theme_id", sa.Text()),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _ck("compilations", "kind", _in("kind", ("bestof", "theme"))),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "compilation_items",
        sa.Column(
            "compilation_id", sa.Integer(), sa.ForeignKey("compilations.id"), primary_key=True
        ),
        sa.Column("position", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id"), nullable=False),
    )
    op.create_table(
        "hooks",
        _id(),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id")),
        sa.Column("compilation_id", sa.Integer(), sa.ForeignKey("compilations.id")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer()),
        sa.Column("chosen", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        _ck("hooks", "engine", _in("engine", ("rules", "llm"))),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "posts",
        _id(),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id")),
        sa.Column("compilation_id", sa.Integer(), sa.ForeignKey("compilations.id")),
        sa.Column("part_number", sa.Integer()),
        sa.Column("family", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("rank_score", sa.Float()),
        sa.Column("video_path", sa.Text()),
        sa.Column("caption", sa.Text()),
        sa.Column("hashtags", sa.Text()),
        sa.Column("hook_id", sa.Integer(), sa.ForeignKey("hooks.id")),
        sa.Column("render_version", sa.Text()),
        sa.Column("planned_at", sa.Text()),
        sa.Column("published_at", sa.Text()),
        sa.Column("platform_url", sa.Text()),
        sa.Column("postiz_post_id", sa.Text()),
        sa.Column("postiz_status", sa.Text()),
        sa.Column("postiz_synced_at", sa.Text()),
        _ck(
            "posts",
            "status",
            _in(
                "status",
                (
                    "planned",
                    "rendered",
                    "ready",
                    "drafted",
                    "scheduled",
                    "published",
                    "discarded",
                    "expired",
                ),
            ),
        ),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "runs",
        _id(),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("ended_at", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text()),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    for table in (
        "runs",
        "posts",
        "hooks",
        "compilation_items",
        "compilations",
        "assignments",
        "segments",
        "scenes",
        "episodes",
        "source_videos",
        "accounts",
        "creators",
        "series",
        "brands",
    ):
        op.drop_table(table)
