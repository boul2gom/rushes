"""Everything the overlays need about a post, gathered once (the `RenderJob` of task 7b)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.config import Layout
from rushes.context import Context
from rushes.core.rng import derive_seed, weighted_choice
from rushes.db.models import Creator, Episode, Hook, Post, Segment, Series
from rushes.errors import RushesError
from rushes.originality.base import RenderJob
from rushes.queue.numbering import is_last_part, part_number
from rushes.render.charter import avatar_path, load_charter


def creator_title(creator_id: str) -> str:
    """Display name of a creator: its id with spaces and capitals (the config has no name)."""
    return creator_id.replace("_", " ").title()


def subject_title(session: Session, episode: Episode) -> str:
    if episode.series_id is not None:
        return session.scalar(select(Series.title).where(Series.id == episode.series_id)) or ""
    return creator_title(episode.creator_id or "")


def other_subject(session: Session, episode: Episode, post_id: int) -> str | None:
    """Title of another active series (creator, for a creator account) of the account, drawn
    with a seed on the post: the same post always names the same one."""
    owner = episode.owner_account_id
    found: dict[str, str] = {}
    for sid, title in session.execute(
        select(Series.id, Series.title)
        .join(Episode, Episode.series_id == Series.id)
        .where(Episode.owner_account_id == owner, Series.status == "active")
        .distinct()
    ):
        if sid != episode.series_id:
            found[f"s:{sid}"] = title
    for cid in session.scalars(
        select(Creator.id)
        .join(Episode, Episode.creator_id == Creator.id)
        .where(Episode.owner_account_id == owner, Creator.status == "active")
        .distinct()
    ):
        if cid != episode.creator_id:
            found[f"c:{cid}"] = creator_title(cid)
    if not found:
        return None
    key = weighted_choice(derive_seed("other-series", post_id), dict.fromkeys(found, 1.0))
    return found[key]


def build_job(
    ctx: Context,
    session: Session,
    post_id: int,
    *,
    layout: Layout,
    duration_ms: int,
    src_w: int,
    src_h: int,
) -> RenderJob:
    post = session.get_one(Post, post_id)
    if post.segment_id is None:
        raise RushesError(f"post {post_id} : pas de passage, rien à habiller")
    segment = session.get_one(Segment, post.segment_id)
    episode = session.get_one(Episode, segment.episode_id)
    account = ctx.config.account(post.account_id)
    brand = ctx.config.brand(account.brand)
    charter = (ctx.config.charters or load_charter(ctx.paths.config_dir, ctx.config))[brand.id]
    copy = charter.texts[brand.language]
    title = subject_title(session, episode).strip()
    if not title:
        raise RushesError(
            f"post {post_id} : titre vide pour le bandeau (série ou créateur sans nom)"
        )
    hook = session.get(Hook, post.hook_id) if post.hook_id is not None else None

    number = part_number(session, post_id) if segment.kind == "part" else None
    last = number is not None and is_last_part(session, post_id)
    next_label: str | None = None
    if number is not None:
        if last:
            other = other_subject(session, episode, post_id)
            next_label = copy.last_part.format(other=other) if other else copy.follow
        else:
            next_label = copy.next_part.format(n=number + 1)
    return RenderJob(
        post_id=post_id,
        account_id=account.id,
        handle=account.handle,
        language=brand.language,
        title=title,
        layout=layout,
        duration_ms=duration_ms,
        part_number=number,
        bestof_title=None if number is not None else title,
        hook_text=hook.text if hook is not None else None,
        next_label=next_label,
        is_last_part=last,
        charter=charter,
        avatar=avatar_path(ctx.paths.config_dir, brand.id),
        src_w=src_w,
        src_h=src_h,
    )
