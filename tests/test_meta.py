"""`meta` step (task 8a): caption, hashtags and cover of rendered posts."""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import Session

from fixtures.generator import Synth, SynthSpec
from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    CREATOR_ACCOUNT,
    ctx_events,
    post_on_video,
    snapshot,
    synthetic_phash,
    with_config,
)
from rushes.context import Context, Filters
from rushes.db.derived import record
from rushes.db.models import Hook, Post, Segment
from rushes.ids import episode_key, post_key
from rushes.meta import meta as meta_module
from rushes.meta.caption import caption_problems
from rushes.meta.meta import MetaStep
from rushes.runner import StepReport, run_step
from rushes.schemas import MetaFile, ScenesFile, Shot, read_json, write_json

SOURCE = SynthSpec(duration_ms=6000, size=(320, 180), fps=10)
SPAN = (1000, 5000)
POOL = {"#tomandjerryclips", "#tomandjerrycartoon", "#classiccartoons"}


def _meta(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, MetaStep(), Filters(**filters))


def _post(ctx: Context, post_id: int) -> dict[str, Any]:
    with ctx.engine.connect() as conn:
        query = text("SELECT * FROM posts WHERE id=:i")
        return dict(conn.execute(query, {"i": post_id}).mappings().one())


def _episode_of(ctx: Context, post_id: int) -> int:
    with ctx.engine.connect() as conn:
        query = (
            "SELECT s.episode_id FROM posts p JOIN segments s ON s.id=p.segment_id WHERE p.id=:i"
        )
        return int(conn.execute(text(query), {"i": post_id}).scalar_one())


def _write_scenes(ctx: Context, post_id: int, motions: tuple[float, float] = (0.1, 0.9)) -> None:
    """Two shots of 3 s with their key frame in the middle."""
    episode = _episode_of(ctx, post_id)
    shots = [
        Shot(id=i, start_ms=a, end_ms=a + 3000, keyframe_ms=a + 1500, motion=m,
             loudness_lufs=None, speech_ratio=0.0, phash=synthetic_phash(i))
        for i, (a, m) in enumerate(zip((0, 3000), motions, strict=True))
    ]  # fmt: skip
    scenes = ScenesFile(version=1, episode_id=episode, duration_ms=6000, detector={}, shots=shots)
    write_json(scenes, ctx.paths.episode_dir(episode_key(episode)) / "scenes.json")


def rendered(
    ctx: Context,
    synth: Synth,
    *,
    owner: str = ACCOUNT,
    scenes: bool = True,
    test: bool = False,
    spec: SynthSpec = SOURCE,
    **kwargs: Any,
) -> int:
    """A 'rendered' post on a synthetic clean.mp4, with scenes.json unless told otherwise."""
    kwargs.setdefault("span", SPAN)
    kwargs.setdefault("layout", "square")
    kwargs.setdefault("render_version", "plate-1+ov-1")  # a finished render
    post_id = post_on_video(ctx, synth(spec), status="rendered", owner=owner, **kwargs)
    if scenes:
        _write_scenes(ctx, post_id)
    with Session(ctx.engine) as session:
        session.get_one(Post, post_id).is_test = test
        session.commit()
    return post_id


def _meta_file(ctx: Context, post_id: int) -> MetaFile:
    return read_json(MetaFile, ctx.paths.renders / f"{post_key(post_id)}.meta.json")


def _cover(ctx: Context, post_id: int) -> Path:
    return ctx.paths.renders / f"{post_key(post_id)}.cover.jpg"


def _messages(ctx: Context) -> list[str]:
    return [str(e.get("message", "")) for e in ctx_events(ctx) if e.get("step") == "meta"]


def test_meta_fills_caption_hashtags_cover_and_file(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    report = _meta(ctx)
    assert (report.done, report.warned, report.failed) == (1, 0, 0), _messages(ctx)
    row = _post(ctx, post_id)
    tags = json.loads(row["hashtags"])
    assert tags[:4] == ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry"]  # R13: required first
    assert len(tags) == 5 and tags[4] in POOL
    caption = row["caption"]
    assert caption.startswith("Tom and Jerry, Part 1. ")  # no hook yet, name first (R12)
    assert caption.endswith(" ".join(tags))
    assert caption_problems(caption, platform="tiktok", name="Tom and Jerry") == []
    assert row["status"] == "rendered"  # meta never moves the status
    with Image.open(_cover(ctx, post_id)) as cover:
        assert cover.size == (1080, 1920) and cover.format == "JPEG"
    meta = _meta_file(ctx, post_id)
    assert (meta.post_id, meta.caption, meta.hashtags) == (post_id, caption, tags)
    assert (meta.hook_applied, meta.hook_alternatives) == (None, [])
    assert not [p for p in ctx.paths.renders.iterdir() if p.name.startswith(".")]  # no temporary


def test_meta_cover_frame_is_the_best_keyframe_in_video_time(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)  # same picture in both shots: the busier shot (0.9) wins
    _meta(ctx)
    # shot 2 keyframe is 4500 ms in the source, the post starts at 1000 ms
    assert _meta_file(ctx, post_id).cover_frame_ms == 3500


def test_meta_cover_follows_the_shot_motion(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    _write_scenes(ctx, post_id, motions=(0.9, 0.1))
    _meta(ctx)
    assert _meta_file(ctx, post_id).cover_frame_ms == 500  # keyframe 1500 ms - 1000 ms


def test_meta_post_without_scenes_uses_the_middle_of_the_segment(
    ctx: Context, synth: Synth
) -> None:
    post_id = rendered(ctx, synth, scenes=False)
    assert _meta(ctx).done == 1
    assert _meta_file(ctx, post_id).cover_frame_ms == 2000  # middle (3000) - start (1000)
    with Image.open(_cover(ctx, post_id)) as cover:
        assert cover.size == (1080, 1920)


def test_meta_unreadable_scenes_falls_back_to_the_middle(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    scenes = ctx.paths.episode_dir(episode_key(_episode_of(ctx, post_id))) / "scenes.json"
    scenes.write_text("{broken", encoding="utf-8")
    assert _meta(ctx).done == 1
    assert _meta_file(ctx, post_id).cover_frame_ms == 2000


def test_meta_test_post_caption_is_prefixed(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth, test=True)
    _meta(ctx)
    caption = _post(ctx, post_id)["caption"]
    assert caption.startswith("NE PAS PUBLIER — Tom and Jerry, Part 1.")
    assert _meta_file(ctx, post_id).caption == caption
    assert caption_problems(caption, platform="tiktok", name="Tom and Jerry") == []


def test_r13_instagram_has_no_fyp_and_the_nostalgia_tag(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth, owner=ACCOUNT_SAME_BRAND)  # st_ig
    _meta(ctx)
    tags = json.loads(_post(ctx, post_id)["hashtags"])
    assert tags[:2] == ["#cartoons", "#nostalgia"]
    assert "#fyp" not in tags and len(tags) == 5
    assert set(tags[2:]) == POOL


def test_r13_nostalgia_tag_comes_from_the_market_study_when_recorded(
    ctx: Context, synth: Synth
) -> None:
    post_id = rendered(ctx, synth, owner=ACCOUNT_SAME_BRAND)
    with Session(ctx.engine) as session:
        record(session, "hashtags.nostalgia_instagram", "cartoons", "#retro", source="market",
               confidence=0.8, at=ctx.clock.now())  # fmt: skip
        session.commit()
    _meta(ctx)
    assert json.loads(_post(ctx, post_id)["hashtags"])[:2] == ["#cartoons", "#retro"]


def test_r13_pool_precedence_config_then_derived_then_default(
    ctx: Context, synth: Synth, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    derived = rendered(ctx, synth)
    with Session(ctx.engine) as session:
        record(session, "hashtags.pool", "tom_and_jerry", ["#studied"], source="market",
               confidence=0.9, at=ctx.clock.now())  # fmt: skip
        session.commit()
    _meta(ctx)
    assert json.loads(_post(ctx, derived)["hashtags"])[4] == "#studied"

    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(branding={"hashtags_pool": ["Slap Stick"]}),
    )
    configured = rendered(with_config(ctx, config_dir), synth)
    _meta(with_config(ctx, config_dir), posts=(configured,))
    assert json.loads(_post(ctx, configured)["hashtags"])[4] == "#slapstick"  # config wins


def test_meta_creator_post_in_french_with_credit(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth, owner=CREATOR_ACCOUNT, creator_id=CREATOR, test=True)
    _meta(ctx)
    row = _post(ctx, post_id)
    tags = json.loads(row["hashtags"])
    assert tags[:2] == ["#fyp", "#squeezie"] and len(tags) == 5
    caption = row["caption"]
    assert caption.startswith("NE PAS PUBLIER — Squeezie, Partie 1. ")
    assert "🎥 Squeezie" in caption
    assert any(
        q in caption for q in ("Qui gagne ?", "Tu l'avais vu venir ?", "Le meilleur moment ?")
    )
    assert caption_problems(caption, platform="tiktok", name="Squeezie") == []


def test_meta_numbers_follow_the_part_order(ctx: Context, synth: Synth) -> None:
    first = rendered(ctx, synth, span=(0, 2500))
    second = post_on_video(
        ctx, synth(SOURCE), status="rendered", span=(3000, 5900), render_version="plate-1+ov-1",
        episode=_episode_of(ctx, first), layout="square",
    )  # fmt: skip
    _meta(ctx)
    assert ", Part 1." in _post(ctx, first)["caption"]
    assert ", Part 2." in _post(ctx, second)["caption"]


def test_meta_hook_is_applied_and_alternatives_kept(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    with Session(ctx.engine) as session:
        segment = session.get_one(Post, post_id).segment_id
        hooks = [Hook(segment_id=segment, text=t, type="t", engine="rules", rank=r)
                 for t, r in (("Wait for the ending 😭", 1), ("Tom vs Jerry: who wins?", 2),
                              ("He did NOT see that coming", 3))]  # fmt: skip
        session.add_all(hooks)
        session.flush()
        session.get_one(Post, post_id).hook_id = hooks[0].id
        session.commit()
    words: list[str] = []
    real = meta_module.render_cover

    def spy(*args: Any, **kwargs: Any) -> None:
        words.append(kwargs["words"])
        real(*args, **kwargs)

    monkeypatch.setattr(meta_module, "render_cover", spy)
    _meta(ctx)
    caption = _post(ctx, post_id)["caption"]
    assert caption.startswith("Wait for the ending 😭 Tom and Jerry, Part 1. ")
    meta = _meta_file(ctx, post_id)
    assert meta.hook_applied == "Wait for the ending 😭"
    assert meta.hook_alternatives == ["Tom vs Jerry: who wins?", "He did NOT see that coming"]
    assert words == ["Wait for the ending"]  # the first four words of the hook, on the cover


def test_meta_cover_words_are_the_name_without_a_hook(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered(ctx, synth)
    words: list[str] = []
    real = meta_module.render_cover
    monkeypatch.setattr(
        meta_module, "render_cover", lambda *a, **k: (words.append(k["words"]), real(*a, **k))
    )
    _meta(ctx)
    assert words == ["Tom and Jerry"]


def test_meta_long_hook_is_dropped_to_keep_the_name_visible(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    with Session(ctx.engine) as session:
        hook = Hook(segment_id=session.get_one(Post, post_id).segment_id, text="x" * 120,
                    type="t", engine="rules", rank=1)  # fmt: skip
        session.add(hook)
        session.flush()
        session.get_one(Post, post_id).hook_id = hook.id
        session.commit()
    _meta(ctx)
    caption = _post(ctx, post_id)["caption"]
    assert "xxx" not in caption and caption.startswith("Tom and Jerry")
    assert _meta_file(ctx, post_id).hook_applied == "x" * 120  # the hook is still the video's


def test_meta_template_override_per_series(ctx: Context, synth: Synth, config_dir: Path) -> None:
    post_id = rendered(ctx, synth)
    (config_dir / "templates").mkdir()
    (config_dir / "templates" / "tom_and_jerry.j2").write_text(
        "{{ name }} | {{ part_label }} | {{ question }} | {{ hashtags }}", encoding="utf-8"
    )
    _meta(with_config(ctx, config_dir))
    # the override is read from the context's config dir
    assert _post(ctx, post_id)["caption"].startswith("Tom and Jerry | Part 1 | ")


def test_r16_meta_rerun_changes_nothing(ctx: Context, synth: Synth) -> None:
    rendered(ctx, synth)
    rendered(ctx, synth, owner=ACCOUNT_SAME_BRAND)
    assert _meta(ctx).done == 2
    before = snapshot(ctx)
    again = _meta(ctx)
    assert (again.done, again.warned, again.skipped, again.failed) == (0, 0, 0, 0)
    assert snapshot(ctx) == before  # same rows, same bytes for meta.json and cover.jpg
    forced = _meta(ctx, force=True)  # recomputing gives the very same bytes
    assert forced.done == 2
    assert snapshot(ctx) == before


def test_meta_only_rendered_posts_without_caption(ctx: Context, synth: Synth) -> None:
    planned = rendered(ctx, synth)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET status='planned' WHERE id=:i"), {"i": planned})
    ready = rendered(ctx, synth)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET status='ready' WHERE id=:i"), {"i": ready})
    done = rendered(ctx, synth)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE posts SET caption='old' WHERE id=:i"), {"i": done})
    todo = rendered(ctx, synth)
    assert _meta(ctx).done == 1
    assert _post(ctx, todo)["caption"] is not None
    assert [_post(ctx, p)["caption"] for p in (planned, ready)] == [None, None]
    assert _post(ctx, done)["caption"] == "old"
    assert _meta(ctx, force=True).done == 2  # rendered posts only: `ready` is never rewritten
    assert _post(ctx, ready)["caption"] is None


def test_r3_meta_skips_a_post_whose_segment_is_not_approved(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    with Session(ctx.engine) as session:
        session.get_one(Segment, session.get_one(Post, post_id).segment_id).status = "needs_review"
        session.commit()
    assert _meta(ctx).done == 0
    assert _post(ctx, post_id)["caption"] is None


def test_meta_posts_filter(ctx: Context, synth: Synth) -> None:
    a, b = rendered(ctx, synth), rendered(ctx, synth)
    assert _meta(ctx, posts=(b,)).done == 1
    assert (_post(ctx, a)["caption"], _post(ctx, b)["caption"] is None) == (None, False)


def test_meta_missing_clean_video_fails_and_writes_nothing(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    (ctx.paths.episode_dir(episode_key(_episode_of(ctx, post_id))) / "clean.mp4").unlink()
    report = _meta(ctx)
    assert report.failed == 1
    assert _post(ctx, post_id)["caption"] is None
    assert not _cover(ctx, post_id).exists()


def test_meta_missing_charter_fails_with_a_message(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth)
    charters = {k: v for k, v in ctx.config.charters.items() if k != "saturday_toons"}
    broken = dataclasses.replace(ctx, config=dataclasses.replace(ctx.config, charters=charters))
    assert _meta(broken).failed == 1
    assert any("charte absente" in str(e.get("message", "")) for e in ctx_events(ctx))
    assert _post(ctx, post_id)["caption"] is None and not _cover(ctx, post_id).exists()


def test_meta_a_change_during_the_work_discards_the_files(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    real = meta_module.render_cover

    def late_discard(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        with ctx.engine.begin() as conn:  # Louis discards the post while the cover is made
            conn.execute(text("UPDATE posts SET status='discarded' WHERE id=:i"), {"i": post_id})

    monkeypatch.setattr(meta_module, "render_cover", late_discard)
    report = _meta(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    row = _post(ctx, post_id)
    assert (row["caption"], row["hashtags"]) == (None, None)
    assert not _cover(ctx, post_id).exists()
    assert not (ctx.paths.renders / f"{post_key(post_id)}.meta.json").exists()
    assert not [p for p in ctx.paths.renders.iterdir() if p.name.startswith(".")]


def test_meta_new_hook_during_the_work_is_not_overwritten(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    real = meta_module.render_cover

    def late_hook(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        with Session(ctx.engine) as session:
            hook = Hook(segment_id=session.get_one(Post, post_id).segment_id, text="New",
                        type="t", engine="rules", rank=1)  # fmt: skip
            session.add(hook)
            session.flush()
            session.get_one(Post, post_id).hook_id = hook.id
            session.commit()

    monkeypatch.setattr(meta_module, "render_cover", late_hook)
    assert _meta(ctx).skipped == 1
    assert _post(ctx, post_id)["caption"] is None
    monkeypatch.setattr(meta_module, "render_cover", real)
    assert _meta(ctx).done == 1  # the next run starts from the new hook
    assert _post(ctx, post_id)["caption"].startswith("New Tom and Jerry")


def test_meta_anamorphic_video_still_gives_a_cover(ctx: Context, synth: Synth) -> None:
    post_id = rendered(ctx, synth, spec=SynthSpec(duration_ms=6000, size=(240, 180), fps=10,
                                                  sar=(4, 3)))  # fmt: skip
    assert _meta(ctx).done == 1
    with Image.open(_cover(ctx, post_id)) as cover:
        assert cover.size == (1080, 1920)


def test_meta_a_caption_written_during_the_work_is_not_overwritten(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    real = meta_module.render_cover

    def other_run(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        with ctx.engine.begin() as conn:  # a concurrent run (or Louis) wrote a caption
            conn.execute(text("UPDATE posts SET caption='theirs' WHERE id=:i"), {"i": post_id})

    monkeypatch.setattr(meta_module, "render_cover", other_run)
    assert _meta(ctx).skipped == 1
    assert _post(ctx, post_id)["caption"] == "theirs"
    assert not _cover(ctx, post_id).exists()


# --- fix round 1 ------------------------------------------------------------------------------


def _set_part(ctx: Context, post_id: int, number: int | None) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(
            text("UPDATE posts SET part_number=:n WHERE id=:i"), {"n": number, "i": post_id}
        )


def _spy_cover(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = meta_module.render_cover

    def spy(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)
        real(*args, **kwargs)

    monkeypatch.setattr(meta_module, "render_cover", spy)
    return calls


def test_r9_meta_uses_the_part_number_fixed_at_render(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = rendered(ctx, synth, span=(0, 2500))
    second = post_on_video(
        ctx, synth(SOURCE), status="rendered", span=(3000, 5900), render_version="plate-1+ov-1",
        episode=_episode_of(ctx, first), layout="square",
    )  # fmt: skip
    _set_part(ctx, first, 1)  # what render wrote on the banners
    _set_part(ctx, second, 2)
    with ctx.engine.begin() as conn:  # the first part is discarded before meta runs
        conn.execute(text("UPDATE posts SET status='discarded' WHERE id=:i"), {"i": first})
    calls = _spy_cover(monkeypatch)
    assert _meta(ctx).done == 1
    row = _post(ctx, second)
    assert row["part_number"] == 2
    assert ", Part 2." in row["caption"]  # not the recomputed rank (1): the video says Part 2
    assert [c["part_number"] for c in calls] == [2]


def test_meta_falls_back_to_the_computed_rank_without_a_stored_number(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    assert _post(ctx, post_id)["part_number"] is None
    calls = _spy_cover(monkeypatch)
    _meta(ctx)
    assert ", Part 1." in _post(ctx, post_id)["caption"]
    assert [c["part_number"] for c in calls] == [1]


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE posts SET part_number=3 WHERE id=:i",
        "UPDATE posts SET layout='full' WHERE id=:i",
    ],
    ids=["part_number", "layout"],
)
def test_meta_a_render_during_the_work_discards_the_files(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    post_id = rendered(ctx, synth)
    _set_part(ctx, post_id, 1)
    real = meta_module.render_cover

    def re_rendered(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        with ctx.engine.begin() as conn:  # a concurrent `render --force` changes the video
            conn.execute(text(change), {"i": post_id})

    monkeypatch.setattr(meta_module, "render_cover", re_rendered)
    report = _meta(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    assert _post(ctx, post_id)["caption"] is None
    assert not _cover(ctx, post_id).exists()
    assert not (ctx.paths.renders / f"{post_key(post_id)}.meta.json").exists()
    assert not [p for p in ctx.paths.renders.iterdir() if p.name.startswith(".")]
    monkeypatch.setattr(meta_module, "render_cover", real)
    assert _meta(ctx).done == 1  # the next run starts from the new state


def test_r13_hashtag_draw_is_seeded_per_post(
    ctx: Context, synth: Synth, config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    from rushes.core.rng import derive_seed
    from rushes.meta.hashtags import resolve_hashtags

    pool = [f"#pool{i}" for i in range(40)]
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(branding={"hashtags_pool": pool}),
    )
    live = with_config(ctx, config_dir)
    posts = [rendered(live, synth), rendered(live, synth)]
    _meta(live)
    fifth = [json.loads(_post(ctx, p)["hashtags"])[4] for p in posts]
    for post_id, tag in zip(posts, fifth, strict=True):
        expected = resolve_hashtags(
            required=["#fyp", "#nostalgiatok", "#cartoons", "{series}"],
            tokens={"{series}": "#tomandjerry"},
            pool=pool,
            platform="tiktok",
            seed=derive_seed(post_id, "hashtags"),
        )
        assert tag == expected[4]
    assert fifth[0] != fifth[1]  # two posts do not draw alike


def test_meta_name_with_inner_spaces_is_flattened_once(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_id = rendered(ctx, synth)
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE series SET title='Tom  and   Jerry' WHERE id='tom_and_jerry'"))
    calls = _spy_cover(monkeypatch)
    report = _meta(ctx)
    assert (report.done, report.warned) == (1, 0), _messages(ctx)  # no false R12 problem
    assert _post(ctx, post_id)["caption"].startswith("Tom and Jerry, Part 1.")
    assert [c["words"] for c in calls] == ["Tom and Jerry"]


# --- fix round 2 ------------------------------------------------------------------------------


def test_meta_waits_for_the_rerender_of_a_reset_post(ctx: Context, synth: Synth) -> None:
    """16b/36 reset `render_version` to NULL: the number on the video is about to change."""
    post_id = rendered(ctx, synth, render_version=None)
    _set_part(ctx, post_id, 1)
    assert MetaStep().select(ctx, Filters()) == []
    assert MetaStep().select(ctx, Filters(force=True)) == []
    report = _meta(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert _post(ctx, post_id)["caption"] is None and not _cover(ctx, post_id).exists()
    with ctx.engine.begin() as conn:  # the render ran again and rewrote part_number
        conn.execute(
            text("UPDATE posts SET render_version='plate-1+ov-1', part_number=2 WHERE id=:i"),
            {"i": post_id},
        )
    assert _meta(ctx).done == 1
    assert ", Part 2." in _post(ctx, post_id)["caption"]
