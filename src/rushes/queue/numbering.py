"""Part numbers (R9): the rank of a part among those that remain visible, with no gap.

No import of `rushes.render` here: `serve` reads this module (task 36). Task 16b adds
`stale_numbers`."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.db.models import Post, Segment

# posts that no longer take a number: a discarded or expired part leaves no gap
GONE = ("discarded", "expired")


def part_number(session: Session, post_id: int) -> int | None:
    """Rank (1, 2, ...) of the post among the 'part' posts of the same episode and account that
    are not discarded or expired, ordered by `start_ms`. None for any other post."""
    own = session.execute(
        select(Post.account_id, Post.status, Segment.episode_id)
        .join(Segment, Segment.id == Post.segment_id)
        .where(Post.id == post_id, Segment.kind == "part")
    ).first()
    if own is None or own.status in GONE:
        return None
    ranked = session.scalars(
        select(Post.id)
        .join(Segment, Segment.id == Post.segment_id)
        .where(
            Segment.kind == "part",
            Segment.episode_id == own.episode_id,
            Post.account_id == own.account_id,
            Post.status.not_in(GONE),
        )
        .order_by(Segment.start_ms, Post.id)
    ).all()
    return ranked.index(post_id) + 1


def is_last_part(session: Session, post_id: int) -> bool:
    """True when no later part of the episode is still to come for the viewer.

    A later 'part' segment counts unless it is rejected or only has discarded or expired posts
    (it will never be seen, so the previous part is the last one shown)."""
    own = session.execute(
        select(Segment.episode_id, Segment.start_ms)
        .join(Post, Post.segment_id == Segment.id)
        .where(Post.id == post_id)
    ).one()
    later = session.execute(
        select(Segment.id, Segment.status).where(
            Segment.kind == "part",
            Segment.episode_id == own.episode_id,
            Segment.start_ms > own.start_ms,
        )
    ).all()
    for segment_id, status in later:
        if status == "rejected":
            continue
        states = set(session.scalars(select(Post.status).where(Post.segment_id == segment_id)))
        if states and states <= set(GONE):
            continue
        return False
    return True
