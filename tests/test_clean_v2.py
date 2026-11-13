"""Cleaning v2 (task 11, C50): theme fingerprint, compilation splitting, burned-in logo."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from conftest import REPO_CONFIG
from fixtures.generator import Synth, SynthSpec, make_theme
from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    CREATOR,
    SERIES,
    ctx_events,
    drop_import,
    make_episode,
    snapshot,
    with_config,
)
from rushes.analyze.analyze import AnalyzeStep
from rushes.clean.clean import CleanStep, _is_cartoon
from rushes.clean.intro import match_reference_frames
from rushes.config import CleanSettings, ConfigError, load_config
from rushes.context import Context, Filters
from rushes.errors import MediaError
from rushes.ids import episode_key
from rushes.ingest.ingest import IngestStep
from rushes.ingest.owner import choose_owner
from rushes.media.detect import detect_black, detect_freeze, detect_silence
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import probe
from rushes.runner import StepReport, run_step
from rushes.schemas import CleanFile, read_json

EditYaml = Callable[[Path, Callable[[Any], Any]], None]
TINY = (160, 90)
SMALL = (320, 180)
# an audio match is not enough on its own: only an image near it confirms the peak
IMAGE_NEEDED = {"theme_sure_threshold": 1.0}
REDUCED = {
    "episode_min_s": 10,
    "compilation_min_s": 30,
    "episode_max_s": 25,
    "typical_episode_s": 15,
}
# 45 s, the 6 s theme at the start of each of three 15 s episodes
THEMED = SynthSpec(duration_ms=45_000, size=TINY, fps=10, theme_ms=[0, 15_000, 30_000])
# 45 s with a 1.2 s black (and silence) gap at 15 s and at 30 s
BLACK_ONLY = SynthSpec(
    duration_ms=45_000,
    size=TINY,
    fps=10,
    black_ms=[(14_400, 15_600), (29_400, 30_600)],  # the music runs over the black
)
# the brief's values (episode_min_s=20, compilation_min_s=60) on a 69 s video of three 23 s episodes
BRIEF = {"episode_min_s": 20, "compilation_min_s": 60}
THEMED_BRIEF = SynthSpec(duration_ms=69_000, size=TINY, fps=10, theme_ms=[0, 23_000, 46_000])
GAPPED = SynthSpec(
    duration_ms=45_000,
    size=TINY,
    fps=10,
    black_ms=[(14_400, 15_600), (29_400, 30_600)],
    silence_ms=[(14_400, 15_600), (29_400, 30_600)],
)


def _configure(
    ctx: Context,
    config_dir: Path,
    edit_yaml: EditYaml,
    *,
    theme: bool = False,
    clean: dict[str, Any] | None = None,
    references: list[Path] | None = None,
    theme_audio: str | list[str] | None = None,
) -> Context:
    """The context with a theme for the series, other cleaning settings, reference images.

    `theme_audio` writes that value (a path or a list of paths) for the series as it is.
    """
    if theme or references or theme_audio:

        def series(data: Any) -> None:
            data["intro_outro"] = {}
            if theme_audio:
                data["intro_outro"]["theme_audio"] = theme_audio
            if theme:
                make_theme(config_dir / "refs" / "theme.wav")
                data["intro_outro"]["theme_audio"] = "refs/theme.wav"
            if references:
                data["intro_outro"]["reference_frames"] = [f"refs/{ref.name}" for ref in references]

        edit_yaml(config_dir / "series" / f"{SERIES}.yaml", series)
    if clean is not None:
        edit_yaml(config_dir / "settings.yaml", lambda data: data.update(clean=clean))
    return with_config(ctx, config_dir)


def _reference(config_dir: Path, video: Path, at_s: int) -> Path:
    ref = config_dir / "refs" / f"frame_{at_s}.png"
    ref.parent.mkdir(exist_ok=True)
    run_ffmpeg(["-ss", str(at_s), "-i", str(video), "-frames:v", "1", str(ref)])
    return ref


def _import(ctx: Context, video: Path, folder: str = SERIES) -> int:
    drop_import(ctx, folder, video)
    assert run_step(ctx, IngestStep(), Filters()).done == 1
    with ctx.engine.connect() as conn:
        return int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())


def _clean(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, CleanStep(preset="ultrafast"), Filters(**filters))


def _rows(ctx: Context) -> list[dict[str, Any]]:
    with ctx.engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("SELECT * FROM episodes ORDER BY id"))]


def _children(ctx: Context, parent: int) -> list[dict[str, Any]]:
    return [r for r in _rows(ctx) if r["parent_episode_id"] == parent]


def _clean_file(ctx: Context, episode_id: int) -> CleanFile:
    return read_json(CleanFile, ctx.paths.episode_dir(episode_key(episode_id)) / "clean.json")


def _failures(ctx: Context) -> list[Any]:
    return [e.get("message") for e in ctx_events(ctx) if e.get("status") == "failed"]


def _warnings(ctx: Context) -> list[str]:
    return [
        e["text"] for e in ctx_events(ctx) if e.get("event") == "message" and e["level"] == "warn"
    ]


def _split(
    ctx: Context,
    config_dir: Path,
    edit_yaml: EditYaml,
    synth: Synth,
    spec: SynthSpec,
    *,
    clean: dict[str, Any] | None = None,
    **kw: Any,
) -> tuple[Context, int]:
    context = _configure(ctx, config_dir, edit_yaml, clean=clean or REDUCED, **kw)
    parent = _import(context, synth(spec))
    report = _clean(context)
    assert (report.done, report.failed) == (1, 0), _failures(context)
    return context, parent


def _close(actual: int, wanted: int, tolerance_ms: int = 250) -> bool:
    return abs(actual - wanted) <= tolerance_ms


# --- splitting --------------------------------------------------------------------------------


def test_compilation_split_by_theme(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(
        ctx, config_dir, edit_yaml, synth, THEMED_BRIEF, theme=True, clean=BRIEF
    )
    rows = _rows(context)
    assert rows[0]["id"] == parent and rows[0]["status"] == "split"
    kids = _children(context, parent)
    assert len(kids) == 3 and all(k["status"] == "cleaned" for k in kids)
    starts = [(k["span_start_ms"], k["span_end_ms"]) for k in kids]
    assert starts[0][0] == 0 and starts[-1][1] == 69_000
    assert _close(starts[0][1], 23_000, 100) and _close(starts[1][1], 46_000, 100)
    for kid, (begin, finish) in zip(kids, starts, strict=True):
        clean = _clean_file(context, kid["id"])
        assert clean.span == (begin, finish)
        assert clean.source_duration_ms == pytest.approx(69_000, abs=100)  # the whole file
        assert _close(clean.kept[0], begin + 6000)  # the theme at its start is gone
        assert clean.kept[1] == finish
        assert [kind for *_, kind in clean.removed] == ["intro"]
        mp4 = context.paths.episode_dir(episode_key(kid["id"])) / "clean.mp4"
        assert _close(probe(mp4).duration_ms, clean.kept[1] - clean.kept[0], 150)
        assert _close(kid["duration_ms"], probe(mp4).duration_ms, 50)


def test_compilation_where_the_first_episode_has_no_theme(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=45_000, size=TINY, fps=10, theme_ms=[15_000, 30_000])
    context, parent = _split(ctx, config_dir, edit_yaml, synth, spec, theme=True)
    kids = _children(context, parent)
    assert len(kids) == 3
    first = _clean_file(context, kids[0]["id"])
    assert first.removed == [] and first.kept == (0, kids[0]["span_end_ms"])


def test_long_compilation_without_refs_splits_on_black_silence(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(ctx, config_dir, edit_yaml, synth, GAPPED)
    kids = _children(context, parent)
    assert len(kids) == 3
    spans = [(k["span_start_ms"], k["span_end_ms"]) for k in kids]
    # each episode stops where the black starts and the next one begins where it ends
    assert spans[0][0] == 0 and spans[-1][1] == 45_000
    assert _close(spans[0][1], 14_400) and _close(spans[1][0], 15_600)
    assert _close(spans[1][1], 29_400) and _close(spans[2][0], 30_600)
    assert all(k["status"] == "cleaned" for k in kids)


def test_the_black_between_episodes_is_in_no_child(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(ctx, config_dir, edit_yaml, synth, GAPPED)
    for kid in _children(context, parent):
        clean = _clean_file(context, kid["id"])
        assert clean.removed == [] and clean.kept == clean.span  # nothing left to cut at an edge
        mp4 = context.paths.episode_dir(episode_key(kid["id"])) / "clean.mp4"
        length = probe(mp4).duration_ms
        assert not any(  # no black picture at either end of the clean.mp4
            start < 300 or end > length - 300 for start, end in detect_black(mp4, min_ms=100)
        )


def test_long_compilation_splits_on_black_alone(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(ctx, config_dir, edit_yaml, synth, BLACK_ONLY)
    spans = [(k["span_start_ms"], k["span_end_ms"]) for k in _children(context, parent)]
    assert len(spans) == 3 and _close(spans[0][1], 14_400) and _close(spans[1][0], 15_600)


def test_a_black_inside_an_episode_far_from_the_typical_length_is_not_a_cut(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # episodes of about 17 s; a fade to black at 26 s would also fit [10 s, 30 s] but is far from
    # the typical 15 s, and the next real boundary leaves it inside the second episode
    spec = SynthSpec(
        duration_ms=55_000,
        size=TINY,
        fps=10,
        black_ms=[(16_400, 17_600), (26_200, 26_800), (35_400, 36_600)],
    )
    wide = {**REDUCED, "episode_max_s": 30}
    context, parent = _split(ctx, config_dir, edit_yaml, synth, spec, clean=wide)
    spans = [(k["span_start_ms"], k["span_end_ms"]) for k in _children(context, parent)]
    assert len(spans) == 3
    assert _close(spans[0][1], 16_400) and _close(spans[1][0], 17_600)
    assert _close(spans[1][1], 35_400) and _close(spans[2][0], 36_600)


def test_unconfirmed_theme_peaks_do_not_cut(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # reference image of a picture that never shows near the theme: the audio alone is not enough
    spec = SynthSpec(
        duration_ms=45_000, size=TINY, fps=10, theme_ms=[0, 15_000, 30_000], shots_ms=[42_000]
    )
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=44)  # the last shot, far from every theme
    context = _configure(
        ctx, config_dir, edit_yaml, theme=True, clean={**REDUCED, **IMAGE_NEEDED}, references=[ref]
    )
    parent = _import(context, video)
    assert _clean(context).done == 1
    assert _rows(context)[0]["status"] == "cleaned"  # one episode, not split
    assert _children(context, parent) == []


def test_only_theme_peaks_with_a_reference_image_near_them_cut(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # the reference image is the second shot: it shows at the theme of 15 s, not at 0 s or 30 s
    spec = SynthSpec(
        duration_ms=45_000,
        size=TINY,
        fps=10,
        theme_ms=[0, 15_000, 30_000],
        shots_ms=[15_000, 30_000],
    )
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=20)
    context = _configure(
        ctx, config_dir, edit_yaml, theme=True, clean={**REDUCED, **IMAGE_NEEDED}, references=[ref]
    )
    parent = _import(context, video)
    assert _clean(context).done == 1
    kids = _children(context, parent)
    assert len(kids) == 2 and kids[0]["span_start_ms"] == 0
    assert _close(kids[0]["span_end_ms"], 15_000, 100) and kids[1]["span_end_ms"] == 45_000
    assert _clean_file(context, kids[0]["id"]).removed == []  # its own theme is unconfirmed


def test_creator_long_video_never_split(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context = _configure(ctx, config_dir, edit_yaml, clean=REDUCED)
    episode = _import(context, synth(GAPPED), CREATOR)
    assert _clean(context).done == 1
    rows = _rows(context)
    assert [(r["id"], r["status"]) for r in rows] == [(episode, "cleaned")]


def test_series_video_owned_by_a_creator_brand_is_never_split(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context = _configure(ctx, config_dir, edit_yaml, clean=REDUCED)
    episode = _import(context, synth(GAPPED))
    with context.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET owner_account_id='ic_tt'"))
    assert _clean(context).done == 1
    assert [r["status"] for r in _rows(context)] == ["cleaned"]
    assert _children(context, episode) == []


def test_is_cartoon_needs_a_known_cartoon_owner(ctx: Context) -> None:
    assert _is_cartoon(ctx, "st_tt") and _is_cartoon(ctx, "tt_ig")
    assert not _is_cartoon(ctx, "ic_tt") and not _is_cartoon(ctx, "pm_ig")
    assert not _is_cartoon(ctx, "unknown_account")  # owner unknown: never guess


def test_long_video_without_theme_or_boundary_is_kept_whole(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context = _configure(ctx, config_dir, edit_yaml, clean=REDUCED)
    episode = _import(context, synth(SynthSpec(duration_ms=40_000, size=TINY, fps=10)))
    assert _clean(context).done == 1
    assert [(r["id"], r["status"]) for r in _rows(context)] == [(episode, "cleaned")]


def test_short_video_is_not_looked_at_for_boundaries(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(
        duration_ms=25_000,
        size=TINY,
        fps=10,
        black_ms=[(12_000, 13_000)],
        silence_ms=[(12_000, 13_000)],
    )
    context = _configure(ctx, config_dir, edit_yaml, clean=REDUCED)  # compilation_min_s = 30
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    assert [(r["id"], r["status"]) for r in _rows(context)] == [(episode, "cleaned")]


# --- parent and children ----------------------------------------------------------------------


def test_children_inherit_owner_and_parent_never_analyzed(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(ctx, config_dir, edit_yaml, synth, GAPPED)
    first, *kids = _rows(context)
    assert first["id"] == parent and first["status"] == "split"
    for k, kid in enumerate([*kids], 1):
        assert kid["owner_account_id"] == first["owner_account_id"]
        assert kid["series_id"] == first["series_id"] and kid["creator_id"] is None
        assert kid["provenance"] == first["provenance"]
        assert kid["file_path"] == first["file_path"]  # one source video
        assert kid["sha256"] == f"{first['sha256']}:{k}"
        assert kid["parent_episode_id"] == parent
    # a split parent has no clean.mp4 and is skipped by analyze, even with --force
    assert not (context.paths.episode_dir(episode_key(parent)) / "clean.mp4").exists()
    step = AnalyzeStep()
    assert step.select(context, Filters()) == [k["id"] for k in kids]
    assert step.select(context, Filters(force=True)) == [k["id"] for k in kids]
    assert parent not in step.select(context, Filters(episode=parent, force=True))


def test_r16_split_rerun_changes_nothing(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, _ = _split(ctx, config_dir, edit_yaml, synth, GAPPED)
    before = snapshot(context)
    report = _clean(context)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(context) == before


def test_force_never_splits_a_split_parent_again(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    context, parent = _split(ctx, config_dir, edit_yaml, synth, GAPPED)
    kids = [k["id"] for k in _children(context, parent)]
    before = [(r["id"], r["sha256"], r["status"]) for r in _rows(context)]
    assert _clean(context, force=True).done == 3  # the three children, cleaned again
    assert [(r["id"], r["sha256"], r["status"]) for r in _rows(context)] == before
    assert _clean(context, force=True, episode=parent).done == 0  # the parent is not selected
    outcome = CleanStep(preset="ultrafast").run(context, parent, force=True)
    assert outcome.status == "skipped" and "déjà découpé" in outcome.message
    # a child with descendants (analyzed) is left alone
    with context.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='analyzed' WHERE id=:i"), {"i": kids[0]})
    report = _clean(context, force=True)
    assert (report.done, report.skipped) == (2, 1)
    assert _children(context, parent)[0]["status"] == "analyzed"


def test_a_child_is_never_split_again(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # two 22.5 s children, each with a black gap in its middle: the gaps are boundaries
    # for a fresh video, never for a child
    spec = SynthSpec(
        duration_ms=45_000,
        size=TINY,
        fps=10,
        theme_ms=[0, 22_500],
        black_ms=[(10_400, 11_600), (32_400, 33_600)],
        silence_ms=[(10_400, 11_600), (32_400, 33_600)],
    )
    context, parent = _split(ctx, config_dir, edit_yaml, synth, spec, theme=True)
    assert len(_children(context, parent)) == 2
    looser = {**REDUCED, "episode_min_s": 5, "compilation_min_s": 10}
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean=looser))
    context = with_config(context, config_dir)
    assert _clean(context, force=True).done == 2
    assert len(_rows(context)) == 3 and len(_children(context, parent)) == 2


def test_force_splits_an_episode_cleaned_before_the_theme_was_configured(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    video = synth(THEMED)
    episode = _import(ctx, video)
    assert _clean(ctx).done == 1  # no theme known: one long episode, as clean v1 did
    folder = ctx.paths.episode_dir(episode_key(episode))
    assert (folder / "clean.mp4").is_file() and _rows(ctx)[0]["status"] == "cleaned"
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET logo_corner='tr'"))  # found when it was cleaned
    context = _configure(ctx, config_dir, edit_yaml, theme=True, clean=REDUCED)
    assert _clean(context).done == 0  # without --force a cleaned episode is left as it is
    assert _clean(context, force=True).done == 1
    assert _rows(context)[0]["status"] == "split"
    assert len(_children(context, episode)) == 3
    assert not (folder / "clean.mp4").exists() and not (folder / "clean.json").exists()
    assert _rows(context)[0]["logo_corner"] is None  # a split parent has no logo of its own


def test_a_child_that_fails_is_taken_up_by_the_next_run(
    ctx: Context,
    config_dir: Path,
    edit_yaml: EditYaml,
    synth: Synth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _configure(ctx, config_dir, edit_yaml, clean=REDUCED)
    parent = _import(context, synth(GAPPED))
    real, calls = CleanStep._encode, []

    def flaky(self: CleanStep, *args: Any, **kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise MediaError("ffmpeg a échoué (code 1)")
        real(self, *args, **kwargs)

    monkeypatch.setattr(CleanStep, "_encode", flaky)
    report = _clean(context)
    assert (report.done, report.failed) == (0, 1)
    kids = _children(context, parent)
    assert [k["status"] for k in kids] == ["cleaned", "imported", "cleaned"]
    assert _rows(context)[0]["status"] == "split"
    monkeypatch.undo()
    assert _clean(context).done == 1  # only the child left behind
    assert [k["status"] for k in _children(context, parent)] == ["cleaned"] * 3
    assert _clean(context).done == 0


# --- theme at the borders ---------------------------------------------------------------------


def test_theme_at_border_removed_as_intro(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[500])
    context = _configure(ctx, config_dir, edit_yaml, theme=True)
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    clean = _clean_file(context, episode)
    assert [kind for *_, kind in clean.removed] == ["intro"]
    assert clean.removed[0][0] == 0 and _close(clean.removed[0][1], 6500)  # chained to the start
    assert _close(clean.kept[0], 6500) and clean.span is None
    assert _rows(context)[0]["status"] == "cleaned"


def test_theme_at_the_end_is_removed_as_outro(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[14_000])
    context = _configure(ctx, config_dir, edit_yaml, theme=True)
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    clean = _clean_file(context, episode)
    assert [kind for *_, kind in clean.removed] == ["outro"]
    assert clean.kept[0] == 0 and _close(clean.kept[1], 14_000)


def test_theme_in_the_middle_of_an_episode_is_kept(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[7000])
    context = _configure(ctx, config_dir, edit_yaml, theme=True)
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    assert _clean_file(context, episode).removed == []


def test_theme_removal_chains_with_the_black_after_it(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # the generic: theme at 0, then 3 s of black; the black is removed because it touches the theme
    # (snapping off: the nominal end of the theme stands and the chaining does the rest)
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0], black_ms=[(6000, 9000)])
    context = _configure(ctx, config_dir, edit_yaml, theme=True, clean={"theme_end_snap_s": 0})
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    clean = _clean_file(context, episode)
    assert [kind for *_, kind in clean.removed] == ["intro", "black"]
    assert _close(clean.kept[0], 9000)


def test_theme_end_snaps_to_the_black_after_it_by_default(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # same video: the generic's end snaps to the end of the black, one `intro` zone up to it
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0], black_ms=[(6000, 9000)])
    context = _configure(ctx, config_dir, edit_yaml, theme=True)
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    clean = _clean_file(context, episode)
    assert [kind for *_, kind in clean.removed] == ["intro"]
    assert _close(clean.kept[0], 9000)


def test_unconfirmed_theme_at_the_border_is_kept(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=24_000, size=TINY, fps=10, theme_ms=[0], shots_ms=[14_000, 20_000])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=17)  # the middle shot: no border, far from the theme
    context = _configure(
        ctx, config_dir, edit_yaml, theme=True, references=[ref], clean=IMAGE_NEEDED
    )
    episode = _import(context, video)
    assert _clean(context).done == 1
    assert _clean_file(context, episode).removed == []


def test_theme_with_a_matching_reference_image_is_removed(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=24_000, size=TINY, fps=10, theme_ms=[0], shots_ms=[14_000, 20_000])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=3)  # the opening shot, under the theme
    context = _configure(
        ctx, config_dir, edit_yaml, theme=True, references=[ref], clean=IMAGE_NEEDED
    )  # the image must confirm: the audio match alone would be sure
    episode = _import(context, video)
    assert _clean(context).done == 1
    clean = _clean_file(context, episode)
    assert clean.removed and clean.removed[0][2] == "intro" and clean.kept[0] >= 6000


@pytest.mark.parametrize("break_it", ["missing", "garbage"])
def test_unusable_theme_file_degrades_with_one_warning(
    ctx: Context,
    config_dir: Path,
    edit_yaml: EditYaml,
    synth: Synth,
    break_it: str,
) -> None:
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0])
    context = _configure(ctx, config_dir, edit_yaml, theme=True)
    theme = config_dir / "refs" / "theme.wav"
    if break_it == "missing":
        theme.unlink()
    else:
        theme.write_bytes(b"this is not audio")
    first = _import(context, synth(spec))
    second = _import(context, synth(SynthSpec(duration_ms=21_000, size=TINY, fps=10)))
    report = _clean(context)
    assert (report.done, report.failed) == (2, 0), _failures(context)
    assert len(_warnings(context)) == 1 and "thème" in _warnings(context)[0]
    assert _clean_file(context, first).removed == []  # no audio detection, the rest goes on
    assert _clean_file(context, second).removed == []


def test_series_without_theme_audio_behaves_like_v1(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0])
    context = _configure(ctx, config_dir, edit_yaml)  # the theme plays, but nobody listens for it
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1
    assert _clean_file(context, episode).removed == []
    assert _warnings(context) == []


# --- logo -------------------------------------------------------------------------------------


def test_r34_logo_marks_episode(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=10_000, size=SMALL, fps=10, shots_ms=[2500, 5000, 7500], logo_corner="tr"
    )
    episode = _import(ctx, synth(spec))
    assert _clean(ctx).done == 1
    assert _rows(ctx)[0]["logo_corner"] == "tr" and _rows(ctx)[0]["id"] == episode


def test_r34_logo_marks_a_creator_episode_too(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=10_000, size=SMALL, fps=10, shots_ms=[2500, 5000, 7500], logo_corner="bl"
    )
    _import(ctx, synth(spec), CREATOR)
    assert _clean(ctx).done == 1
    assert _rows(ctx)[0]["logo_corner"] == "bl"  # whatever the provenance


def test_no_logo_on_clean_video(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, fps=10, shots_ms=[2500, 5000, 7500])
    _import(ctx, synth(spec))
    assert _clean(ctx).done == 1
    assert _rows(ctx)[0]["logo_corner"] is None


def test_static_slideshow_not_a_logo(ctx: Context, synth: Synth) -> None:
    spec = SynthSpec(duration_ms=10_000, size=SMALL, fps=10, logo_corner="tr")  # one still picture
    _import(ctx, synth(spec))
    assert _clean(ctx).done == 1
    assert _rows(ctx)[0]["logo_corner"] is None


def test_logo_is_measured_on_each_child_and_not_on_the_split_parent(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    spec = SynthSpec(
        duration_ms=45_000,
        size=SMALL,
        fps=5,
        shots_ms=list(range(1500, 45_000, 1500)),  # lively enough that no flat area is static
        theme_ms=[0, 15_000, 30_000],
        logo_corner="tr",
    )
    context, _ = _split(ctx, config_dir, edit_yaml, synth, spec, theme=True)
    rows = _rows(context)
    assert rows[0]["logo_corner"] is None
    assert [r["logo_corner"] for r in rows[1:]] == ["tr", "tr", "tr"]


def test_split_parent_is_not_stock_for_the_owner_choice(ctx: Context, session: Session) -> None:
    first, second = sorted((ACCOUNT, ACCOUNT_SAME_BRAND))
    for _ in range(2):
        make_episode(session, owner=first).status = "split"  # parents: their children count
    make_episode(session, owner=second)
    session.flush()
    today = ctx.clock.now().date()
    owner = choose_owner(session, ctx.config, series_id=SERIES, creator_id=None, today=today)
    assert owner == first  # 0 episodes in progress against 1


# --- building blocks: detectors on a span, reference images on given windows ----------------


def test_detectors_with_a_span_count_from_its_start(synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=20_000,
        size=TINY,
        fps=10,
        black_ms=[(5000, 8000)],
        silence_ms=[(5000, 8000)],
        title_card_ms=(12_000, 16_000),
    )
    video = synth(spec)
    (black,) = detect_black(video, min_ms=500, span=(4000, 12_000))
    (silence,) = detect_silence(video, min_ms=500, span=(4000, 12_000))
    assert _close(black[0], 1000, 100) and _close(black[1], 4000, 100)
    assert _close(silence[0], 1000, 100) and _close(silence[1], 4000, 100)
    assert detect_black(video, min_ms=500, span=(10_000, 20_000)) == []  # the black is outside
    # a silence still open at the end of the span closes at the length of the span
    (open_one,) = detect_silence(video, min_ms=500, span=(6000, 7000))
    assert open_one[0] <= 100 and open_one[1] == 1000
    freezes = detect_freeze(video, min_ms=1500, span=(10_000, 20_000))
    assert any(_close(s, 2000, 150) and _close(e, 6000, 150) for s, e in freezes)


def test_reference_frames_on_given_windows_and_spans(
    config_dir: Path, synth: Synth, tmp_path: Path
) -> None:
    spec = SynthSpec(duration_ms=30_000, size=TINY, fps=10, shots_ms=[10_000, 20_000])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=15)  # the middle shot, 10 s to 20 s
    found = match_reference_frames(video, [ref], tmp_dir=tmp_path)
    assert len(found) == 1  # the default windows (90 s) cover this short video
    inside = match_reference_frames(video, [ref], windows=[(8000, 22_000)], tmp_dir=tmp_path)
    assert (
        len(inside) == 1 and _close(inside[0][0], 10_000, 600) and _close(inside[0][1], 20_000, 600)
    )
    assert match_reference_frames(video, [ref], windows=[(0, 6000)], tmp_dir=tmp_path) == []
    # a part of the file: its own edges. The shot touches the start of (10 s, 30 s)
    child = match_reference_frames(video, [ref], span=(10_000, 30_000), tmp_dir=tmp_path)
    assert len(child) == 1
    assert _close(child[0][0], 10_000, 600) and _close(child[0][1], 20_000, 600)
    # ... and nothing is looked for outside the span
    assert match_reference_frames(video, [ref], span=(21_000, 30_000), tmp_dir=tmp_path) == []


# --- settings ---------------------------------------------------------------------------------


def test_clean_settings_defaults_are_those_of_the_plan() -> None:
    s = CleanSettings()
    assert (s.theme_threshold, s.theme_hop_ms, s.compilation_min_s) == (0.6, 50, 1200)
    assert (s.episode_min_s, s.episode_max_s, s.typical_episode_s) == (240, 1800, 420)
    assert (s.boundary_min_ms, s.logo_samples) == (500, 120)
    assert (s.logo_static_std, s.logo_static_share, s.logo_min_edge_ratio) == (6.0, 0.9, 0.01)


def test_clean_settings_are_read_from_settings_yaml(config_dir: Path, edit_yaml: EditYaml) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean={"theme_threshold": 0.75}))
    assert load_config(config_dir).settings.clean.theme_threshold == 0.75
    assert load_config(REPO_CONFIG).settings.clean == CleanSettings()  # the key is optional


@pytest.mark.parametrize(
    "bad",
    [
        {"theme_threshold": 0},
        {"theme_threshold": 1.5},
        {"theme_hop_ms": 5},
        {"theme_hop_ms": 64},
        {"theme_hop_ms": 100},
        {"episode_min_s": 0},
        {"episode_min_s": 600, "episode_max_s": 300},
        {"logo_samples": 2},
        {"logo_static_share": 0.5},
        {"logo_static_share": 1.2},
        {"logo_min_edge_ratio": 0},
        {"unknown_key": 1},
    ],
)
def test_clean_settings_refuse_nonsense(config_dir: Path, edit_yaml: EditYaml, bad: Any) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean=bad))
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)
    assert "clean" in str(excinfo.value)
