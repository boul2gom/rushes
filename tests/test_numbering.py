"""Part numbers (R9): rank among the visible parts of an episode on one account."""

import ast
from pathlib import Path

from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT_OTHER_BRAND,
    make_episode,
    make_post,
    make_segment,
)
from rushes.queue import numbering
from rushes.queue.numbering import is_last_part, part_number


def _parts(session: Session, n: int = 4):  # type: ignore[no-untyped-def]
    episode = make_episode(session)
    segments = [
        make_segment(session, episode, "part", i * 10_000, i * 10_000 + 8_000) for i in range(n)
    ]
    return episode, segments


def test_part_number_is_the_rank_by_start_ms(session: Session) -> None:
    _, segments = _parts(session)
    # created out of order: the rank follows start_ms, not the post id
    posts = [make_post(session, s) for s in reversed(segments)]
    ranks = {p.id: part_number(session, p.id) for p in posts}
    assert [ranks[p.id] for p in reversed(posts)] == [1, 2, 3, 4]


def test_part_number_ignores_discarded_posts(session: Session) -> None:
    _, segments = _parts(session)
    posts = [make_post(session, s) for s in segments]
    assert [part_number(session, p.id) for p in posts] == [1, 2, 3, 4]
    posts[1].status = "discarded"
    session.flush()
    assert [part_number(session, p.id) for p in posts] == [1, None, 2, 3]  # no gap
    posts[2].status = "expired"
    session.flush()
    assert [part_number(session, p.id) for p in posts] == [1, None, None, 2]


def test_part_number_keeps_published_parts(session: Session) -> None:
    _, segments = _parts(session, 3)
    posts = [make_post(session, s, status="published") for s in segments]
    assert [part_number(session, p.id) for p in posts] == [1, 2, 3]


def test_part_number_is_per_episode_and_per_account(session: Session) -> None:
    _, first = _parts(session, 2)
    other_episode = make_episode(session)
    other = make_segment(session, other_episode, "part", 0, 5_000)
    make_post(session, first[0])
    a = make_post(session, first[1])
    b = make_post(session, other)
    elsewhere = make_post(session, first[0], ACCOUNT_OTHER_BRAND)  # another account's post
    assert part_number(session, a.id) == 2  # the other account's post does not count
    assert part_number(session, b.id) == 1
    assert part_number(session, elsewhere.id) == 1


def test_part_number_is_none_for_a_post_that_is_not_a_part(session: Session) -> None:
    episode = make_episode(session)
    item = make_segment(session, episode, "bestof_item", 0, 5_000)
    post = make_post(session, item)
    assert part_number(session, post.id) is None
    assert part_number(session, 99_999) is None


def test_is_last_part_looks_at_later_parts_of_the_episode(session: Session) -> None:
    _, segments = _parts(session, 3)
    posts = [make_post(session, s) for s in segments]
    assert [is_last_part(session, p.id) for p in posts] == [False, False, True]


def test_is_last_part_skips_later_parts_that_will_never_be_seen(session: Session) -> None:
    _, segments = _parts(session, 4)
    posts = [make_post(session, s) for s in segments[:3]]  # the 4th has no post yet
    assert is_last_part(session, posts[2].id) is False  # a part is still to come
    segments[3].status = "rejected"
    posts[2].status = "planned"
    session.flush()
    assert is_last_part(session, posts[2].id) is True  # rejected: never coming
    segments[3].status = "approved"
    late = make_post(session, segments[3], status="discarded")
    session.flush()
    assert late.id and is_last_part(session, posts[2].id) is True  # only a discarded post
    make_post(session, segments[3])  # a live post appears
    assert is_last_part(session, posts[2].id) is False


def test_numbering_never_imports_the_render_package() -> None:
    """`serve` reads this module (task 36): no `rushes.render` import in it."""
    tree = ast.parse(Path(numbering.__file__ or "").read_text(encoding="utf-8"))
    imported = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ] + [a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names]
    assert not [m for m in imported if m.startswith("rushes.render")]
