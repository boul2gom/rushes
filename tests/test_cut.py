"""`cut` step (task 6): parts of an analyzed episode, written as 'part' segments."""

import json
import math
import random
from itertools import pairwise
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT,
    analyzed_episode,
    assign_raw,
    ctx_events,
    make_episode,
    snapshot,
    with_config,
)
from rushes.config import CutSettings
from rushes.context import Context, Filters
from rushes.core.rng import derive_seed, draw_target_ms
from rushes.cut.parts import CutStep, plan_parts
from rushes.db.models import Segment
from rushes.ids import episode_key
from rushes.runner import StepReport, run_step
from rushes.schemas import AudioFile, LoudPoint, Shot

MIN_MS, MAX_MS = 65_000, 100_000


def _cut(ctx: Context, **filters: object) -> StepReport:
    return run_step(ctx, CutStep(), Filters(**filters))  # type: ignore[arg-type]


def _parts(ctx: Context, episode_id: int) -> list[dict[str, Any]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, start_ms, end_ms, family, target_ms, seed, score, status, "
                "phash_json FROM segments WHERE episode_id=:e ORDER BY start_ms"
            ),
            {"e": episode_id},
        ).mappings()
        return [dict(r) for r in rows]


def _bounds(parts: list[dict[str, Any]]) -> list[int]:
    return [parts[0]["start_ms"], *(p["end_ms"] for p in parts)]


def test_r43_episode_always_gets_parts(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)  # shots every 5 s
    report = _cut(ctx)
    assert (report.done, report.failed) == (1, 0)
    parts = _parts(ctx, episode_id)
    assert len(parts) >= 2
    for a, b in pairwise(parts):
        assert a["end_ms"] == b["start_ms"]  # consecutive: end k == start k+1
    for part in parts:
        assert MIN_MS <= part["end_ms"] - part["start_ms"] <= MAX_MS
        assert (part["kind"], part["family"], part["status"]) == ("part", "monetizable", "proposed")
        assert 0 < part["score"] <= 1
        first, last = part["start_ms"] // 5000, (part["end_ms"] - 1) // 5000
        assert json.loads(part["phash_json"]) == [f"{i:016x}" for i in range(first, last + 1)]


def test_r6_target_seed_recorded(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    parts = _parts(ctx, episode_id)
    assert all(p["seed"] == derive_seed(episode_id, "cut") for p in parts)
    assert [p["target_ms"] for p in parts] == [
        draw_target_ms(derive_seed(episode_id, "cut", k), 65, 100) for k in range(len(parts))
    ]  # the recorded seed reproduces every target
    again = _cut(ctx, force=True)  # proposed parts nobody uses yet: recomputed, same draws
    assert again.done == 1
    assert _bounds(_parts(ctx, episode_id)) == _bounds(parts)
    assert [p["target_ms"] for p in _parts(ctx, episode_id)] == [p["target_ms"] for p in parts]


def test_r5_cut_step_keeps_part_bounds_out_of_speech(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000, speech=[(60_000, 75_000)])
    assert _cut(ctx).done == 1
    assert not any(59_850 <= b <= 75_150 for b in _bounds(_parts(ctx, episode_id)))


def test_cut_short_episode_gives_no_part_and_warns(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=50_000)
    report = _cut(ctx)
    assert (report.done, report.warned) == (0, 1)
    assert _parts(ctx, episode_id) == []
    warned = [e for e in ctx_events(ctx) if e.get("status") == "warned"]
    assert "épisode trop court pour la famille monetizable (50 s)" in warned[0]["message"]


def test_cut_without_shot_changes_uses_quiet_points(ctx: Context) -> None:
    # one shot for 3 min: the only points are 0 and 180 s, so the quiet points make the cut
    loudness = [(t, -20.0 + 6 * math.sin(t / 3000)) for t in range(3000, 180_001, 100)]
    episode_id = analyzed_episode(ctx, duration_ms=180_000, shot_starts=[0], loudness=loudness)
    assert _cut(ctx).done == 1
    parts = _parts(ctx, episode_id)
    assert len(parts) >= 1
    assert all(MIN_MS <= p["end_ms"] - p["start_ms"] <= MAX_MS for p in parts)
    assert parts[0]["start_ms"] == 0


def test_cut_without_shot_changes_and_no_loudness_warns(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=180_000, shot_starts=[0])
    report = _cut(ctx)
    assert (report.done, report.warned) == (0, 1)
    assert _parts(ctx, episode_id) == []
    warned = [e for e in ctx_events(ctx) if e.get("status") == "warned"]
    assert "aucun point de coupe admissible" in warned[0]["message"]


def test_r16_cut_rerun_changes_nothing(ctx: Context) -> None:
    analyzed_episode(ctx, duration_ms=240_000)
    assert _cut(ctx).done == 1
    before = snapshot(ctx)
    again = _cut(ctx)
    assert (again.done, again.failed) == (0, 0)
    assert snapshot(ctx) == before


def test_cut_force_spares_an_episode_whose_part_is_assigned(ctx: Context) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    with Session(ctx.engine) as session:
        segment = session.get(Segment, _parts(ctx, episode_id)[0]["id"])
        assert segment is not None
        assign_raw(session, segment, ACCOUNT)
        session.commit()
    before = snapshot(ctx)
    report = _cut(ctx, force=True)
    assert (report.done, report.skipped) == (0, 1)
    assert (
        "déjà attribué"
        in [e for e in ctx_events(ctx) if e.get("status") == "skipped"][-1]["message"]
    )
    assert snapshot(ctx) == before


def test_cut_only_takes_analyzed_episodes_and_needs_the_analysis_files(ctx: Context) -> None:
    with Session(ctx.engine) as session:
        episode = make_episode(session)
        episode.status = "cleaned"
        session.commit()
    assert _cut(ctx).done == 0  # not analyzed: not selected
    broken = analyzed_episode(ctx, duration_ms=240_000)
    (ctx.paths.episode_dir(episode_key(broken)) / "scenes.json").unlink()
    report = _cut(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert _parts(ctx, broken) == []


def test_r6_plan_parts_never_leaves_the_bounds_over_200_episodes() -> None:
    settings = CutSettings()
    parts_seen = shifted = 0
    for episode in range(200):
        rng = random.Random(episode)
        duration = rng.randint(30_000, 300_000)
        starts = [0]
        if rng.random() > 0.1:  # some episodes have a single shot: the quiet-point fallback
            while starts[-1] + 7000 < duration:
                starts.append(starts[-1] + rng.randint(1000, 7000))
        ends = [*starts[1:], duration]
        shots = [
            Shot(
                id=i,
                start_ms=a,
                end_ms=b,
                keyframe_ms=a,
                motion=rng.choice([0.0, 0.05, 0.6]),
                loudness_lufs=None,
                speech_ratio=0.0,
                phash=f"{i:016x}",
            )
            for i, (a, b) in enumerate(zip(starts, ends, strict=True))
        ]
        speech = []
        for _ in range(rng.randint(0, 3)):
            a = rng.randint(0, duration - 1000)
            speech.append((a, min(duration, a + rng.randint(3000, 15_000))))
        audio = AudioFile(
            version=1,
            episode_id=episode,
            integrated_lufs=-20.0,
            loudness=[
                LoudPoint(t_ms=t, lufs=rng.uniform(-30, -10)) for t in range(0, duration, 500)
            ],
            speech=speech,
        )
        parts = plan_parts(
            episode_id=episode,
            shots=shots,
            audio=audio,
            duration_ms=duration,
            bounds_s=(65, 100),
            s=settings,
        )
        for a, b in pairwise(parts):
            assert a.end_ms == b.start_ms
        for part in parts:
            assert MIN_MS <= part.end_ms - part.start_ms <= MAX_MS
        if parts:
            assert parts[0].start_ms <= settings.start_shift_ms
            assert duration - parts[-1].end_ms < MIN_MS
            shifted += parts[0].start_ms > 0
        margin = settings.speech_margin_ms
        for bound in {x for part in parts for x in (part.start_ms, part.end_ms)} - {0, duration}:
            # finite cost: never inside a (widened) speech span
            assert not any(a - margin <= bound <= b + margin for a, b in speech)
        parts_seen += len(parts)
    assert parts_seen > 100 and shifted > 0  # neither the property nor the shift is vacuous


def test_cut_force_without_partition_drops_unused_parts_and_warns(
    ctx: Context, config_dir: Path, edit_yaml: Any
) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    assert _parts(ctx, episode_id)
    # family bounds changed: no multiple of 5 s lies 61 to 62 s after a cut, so no partition exists
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["families"]["monetizable"].update(min_s=61, max_s=62),
    )
    report = run_step(with_config(ctx, config_dir), CutStep(), Filters(force=True))
    assert (report.done, report.warned) == (0, 1)
    assert _parts(ctx, episode_id) == []  # an out-of-bounds part is never kept (R6)


def test_cut_force_with_a_used_part_keeps_everything_even_without_partition(
    ctx: Context, config_dir: Path, edit_yaml: Any
) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    with Session(ctx.engine) as session:
        segment = session.get(Segment, _parts(ctx, episode_id)[0]["id"])
        assert segment is not None
        assign_raw(session, segment, ACCOUNT)
        session.commit()
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["families"]["monetizable"].update(min_s=61, max_s=62),
    )
    before = snapshot(ctx)
    report = run_step(with_config(ctx, config_dir), CutStep(), Filters(force=True))
    assert (report.done, report.skipped) == (0, 1)
    assert snapshot(ctx) == before


def test_cut_force_on_a_too_short_episode_drops_unused_parts_and_warns(
    ctx: Context, config_dir: Path, edit_yaml: Any
) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    assert _parts(ctx, episode_id)
    edit_yaml(  # the family now starts at 250 s: the 240 s episode is too short
        config_dir / "accounts.yaml",
        lambda d: d["families"]["monetizable"].update(min_s=250, max_s=300),
    )
    report = run_step(with_config(ctx, config_dir), CutStep(), Filters(force=True))
    assert (report.done, report.warned) == (0, 1)
    assert _parts(ctx, episode_id) == []
    warned = [e for e in ctx_events(ctx) if e.get("status") == "warned"]
    assert "épisode trop court pour la famille monetizable (240 s)" in warned[-1]["message"]


def test_cut_force_on_a_too_short_episode_keeps_used_parts(
    ctx: Context, config_dir: Path, edit_yaml: Any
) -> None:
    episode_id = analyzed_episode(ctx, duration_ms=240_000)
    _cut(ctx)
    with Session(ctx.engine) as session:
        segment = session.get(Segment, _parts(ctx, episode_id)[0]["id"])
        assert segment is not None
        assign_raw(session, segment, ACCOUNT)
        session.commit()
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["families"]["monetizable"].update(min_s=250, max_s=300),
    )
    before = snapshot(ctx)
    report = run_step(with_config(ctx, config_dir), CutStep(), Filters(force=True))
    assert (report.done, report.skipped) == (0, 1)  # used parts: "déjà attribué" comes first
    assert snapshot(ctx) == before
