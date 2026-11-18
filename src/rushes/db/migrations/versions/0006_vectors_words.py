"""Scene vectors (CLIP) and transcribed words.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scene_vectors",
        sa.Column(
            "scene_id",
            sa.Integer(),
            sa.ForeignKey("scenes.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "words",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("word", sa.Text(), nullable=False),
        sa.Column("prob", sa.Float(), nullable=False),
        sa.Column("sentence_id", sa.Integer()),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_words_episode_id_start_ms", "words", ["episode_id", "start_ms"])


def downgrade() -> None:
    op.drop_index("ix_words_episode_id_start_ms", table_name="words")
    op.drop_table("words")
    op.drop_table("scene_vectors")
