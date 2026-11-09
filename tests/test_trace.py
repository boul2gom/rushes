"""`post_trace` (task 8b): the traceability of R17, read from the files and rows that exist."""

from dataclasses import fields

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from helpers import ACCOUNT, make_episode, make_post, make_segment
from rushes.context import Context
from rushes.db.models import Compilation, CompilationItem, Hook, Post
from rushes.db.trace import PostTrace, post_trace
from rushes.errors import RushesError
from rushes.ids import episode_key
from rushes.schemas import CleanFile, write_json

KEPT = (2_000, 400_000)


def _clean(ctx: Context, episode_id: int, kept: tuple[int, int] = KEPT) -> None:
    folder = ctx.paths.episode_dir(episode_key(episode_id))
    write_json(
        CleanFile(version=1, episode_id=episode_id, source_duration_ms=420_000, kept=kept,
                  removed=[]),
        folder / "clean.json",
    )  # fmt: skip


def _trace(ctx: Context, post_id: int) -> PostTrace:
    with Session(ctx.engine) as session:
        return post_trace(session, ctx.paths, post_id)


def _part(ctx: Context, *, hook: bool = False) -> int:
    with Session(ctx.engine) as session:
        episode = make_episode(session)
        episode.provenance = "official"
        segment = make_segment(session, episode, "part", 10_000, 80_000)
        segment.seed = 1234
        post = make_post(
            session, segment, ACCOUNT, status="rendered", render_version="plate-1+ov-1"
        )
        if hook:
            row = Hook(segment_id=segment.id, text="Wait for it", type="curiosity", engine="rules")
            session.add(row)
            session.flush()
            post.hook_id = row.id
        session.commit()
        episode_id, post_id = episode.id, post.id
    _clean(ctx, episode_id)
    return post_id


def test_r17_post_traceability_complete(ctx: Context) -> None:
    post_id = _part(ctx)
    trace = _trace(ctx, post_id)
    with ctx.engine.connect() as conn:
        episode = int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())
    assert trace == PostTrace(
        post_id=post_id, episode_id=episode, provenance="official", source_offset_ms=2_000,
        start_ms=10_000, end_ms=80_000, seed=1234, signal_level=None,
        render_version="plate-1+ov-1", hook_type=None, hook_engine=None,
        items=((episode, 10_000, 80_000),),
    )  # fmt: skip
    # a part has no hook before task 14 and no signal level (best-of only): all else is set
    unset = {f.name for f in fields(trace) if getattr(trace, f.name) is None}
    assert unset == {"signal_level", "hook_type", "hook_engine"}


def test_r17_hook_type_and_engine_are_traced_when_there_is_a_hook(ctx: Context) -> None:
    trace = _trace(ctx, _part(ctx, hook=True))
    assert (trace.hook_type, trace.hook_engine) == ("curiosity", "rules")


def _bestof(ctx: Context, levels: tuple[int | None, ...]) -> tuple[int, list[int]]:
    with Session(ctx.engine) as session:
        first, second = make_episode(session), make_episode(session)
        spans = [(first, 30_000, 36_000), (second, 5_000, 12_000), (first, 90_000, 95_000)]
        segments = []
        for (episode, start, end), level in zip(spans, levels, strict=True):
            segment = make_segment(session, episode, "bestof_item", start, end)
            segment.signal_level, segment.seed = level, 7
            segments.append(segment)
        compilation = Compilation(kind="bestof", account_id=ACCOUNT, status="proposed")
        session.add(compilation)
        session.flush()
        for position, segment in reversed(list(enumerate(segments))):  # not inserted in order
            session.add(
                CompilationItem(
                    compilation_id=compilation.id, position=position, segment_id=segment.id
                )
            )
        post = Post(
            account_id=ACCOUNT, compilation_id=compilation.id, status="rendered",
            render_version="plate-1+ov-1",
        )  # fmt: skip
        session.add(post)
        session.commit()
        episodes = [first.id, second.id]
        post_id = post.id
    for episode_id in episodes:
        _clean(ctx, episode_id, kept=(500, 300_000))
    return post_id, episodes


def test_r17_bestof_traces_every_item(ctx: Context) -> None:
    post_id, (first, second) = _bestof(ctx, (2, 1, 3))
    trace = _trace(ctx, post_id)
    assert trace.items == (
        (first, 30_000, 36_000),
        (second, 5_000, 12_000),
        (first, 90_000, 95_000),
    )
    assert (trace.episode_id, trace.start_ms, trace.end_ms) == (first, 30_000, 95_000)
    assert trace.signal_level == 3  # the weakest level reached
    assert (trace.seed, trace.source_offset_ms, trace.provenance) == (7, 500, "local")
    assert trace.render_version == "plate-1+ov-1"


def test_r17_bestof_without_levels_leaves_the_level_empty(ctx: Context) -> None:
    post_id, _ = _bestof(ctx, (None, None, None))
    assert _trace(ctx, post_id).signal_level is None


def test_r17_trace_needs_a_render_a_clean_json_and_something_to_trace(ctx: Context) -> None:
    post_id = _part(ctx)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version=NULL"))
    with pytest.raises(RushesError, match="pas encore rendu"):
        _trace(ctx, post_id)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET render_version='plate-1+ov-1'"))
    with ctx.engine.connect() as conn:
        episode = int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())
    (ctx.paths.episode_dir(episode_key(episode)) / "clean.json").unlink()
    with pytest.raises(RushesError, match=r"clean\.json"):
        _trace(ctx, post_id)
    with pytest.raises(RushesError, match="introuvable"):
        _trace(ctx, 9999)
    with Session(ctx.engine) as session:
        loose = Post(account_id=ACCOUNT, status="rendered", render_version="x")
        session.add(loose)
        session.commit()
        loose_id = loose.id
    with pytest.raises(RushesError, match="rien à tracer"):
        _trace(ctx, loose_id)
