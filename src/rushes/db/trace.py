"""Traceability of a post (R17, task 8b): where each second of it comes from, and how it was made.

Read from what already exists: the post, its segments and hook in the database, the offset of
`clean.mp4` in `clean.json`. A field nobody produces yet stays None: the hook before task 14, the
signal level of a part (it belongs to best-of). The audit (task 40a) and `rushes serve` read this.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.db.models import CompilationItem, Episode, Hook, Post, Segment
from rushes.errors import RushesError
from rushes.ids import episode_key, post_key
from rushes.paths import Paths
from rushes.schemas import CleanFile, read_json


@dataclass(frozen=True)
class PostTrace:
    post_id: int
    episode_id: int  # the episode of the first passage
    provenance: str
    source_offset_ms: int  # source time of clean.mp4 time 0 (task 24 adds the interior holes)
    start_ms: int  # in clean.mp4 time: start of the first passage
    end_ms: int  # end of the last passage
    seed: int | None
    signal_level: int | None
    render_version: str
    hook_type: str | None
    hook_engine: str | None
    items: tuple[tuple[int, int, int], ...] = ()  # (episode_id, start_ms, end_ms) of each passage


def _offset_ms(paths: Paths, episode_id: int) -> int:
    path = paths.episode_dir(episode_key(episode_id)) / "clean.json"
    try:
        return read_json(CleanFile, path).kept[0]
    except (OSError, ValueError) as exc:
        raise RushesError(
            f"{episode_key(episode_id)} : clean.json absent ou illisible ({exc}), "
            "décalage source inconnu",
            hint="Relancer `rushes clean --force` pour cet épisode.",
        ) from exc


def _segments(session: Session, post: Post) -> list[Segment]:
    """The passages of the post, in playing order: its own, or those of its compilation."""
    if post.segment_id is not None:
        return [session.get_one(Segment, post.segment_id)]
    if post.compilation_id is None:
        raise RushesError(f"{post_key(post.id)} : ni passage ni compilation, rien à tracer")
    ranked = session.scalars(
        select(Segment)
        .join(CompilationItem, CompilationItem.segment_id == Segment.id)
        .where(CompilationItem.compilation_id == post.compilation_id)
        .order_by(CompilationItem.position)
    ).all()
    if not ranked:
        raise RushesError(f"{post_key(post.id)} : compilation sans passage, rien à tracer")
    return list(ranked)


def post_trace(session: Session, paths: Paths, post_id: int) -> PostTrace:
    """The trace of a post; RushesError (French) when it cannot be built.

    For a best-of or a compilation: `start_ms`/`end_ms` are the start of the first passage and the
    end of the last, `episode_id` and `seed` those of the first, `signal_level` the weakest of the
    levels recorded (the highest number), and `items` lists every passage."""
    post = session.get(Post, post_id)
    if post is None:
        raise RushesError(f"{post_key(post_id)} : post introuvable")
    if post.render_version is None:
        raise RushesError(f"{post_key(post_id)} : pas encore rendu, version de rendu inconnue")
    segments = _segments(session, post)
    first, last = segments[0], segments[-1]
    episode = session.get_one(Episode, first.episode_id)
    hook = session.get(Hook, post.hook_id) if post.hook_id is not None else None
    levels = [s.signal_level for s in segments if s.signal_level is not None]
    return PostTrace(
        post_id=post_id,
        episode_id=first.episode_id,
        provenance=episode.provenance,
        source_offset_ms=_offset_ms(paths, first.episode_id),
        start_ms=first.start_ms,
        end_ms=last.end_ms,
        seed=first.seed,
        signal_level=max(levels) if levels else None,
        render_version=post.render_version,
        hook_type=hook.type if hook is not None else None,
        hook_engine=hook.engine if hook is not None else None,
        items=tuple((s.episode_id, s.start_ms, s.end_ms) for s in segments),
    )
