"""Theme variants of a series (task 11, round 4): other eras have other generics."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fixtures.generator import Generic, Synth, SynthSpec, make_theme
from helpers import SERIES, snapshot
from rushes.clean.clean import (
    ThemeLoader,
    ThemePeak,
    _one_peak_per_place,
    _snapped_end,
    _zone_candidates,
    cartoon_removed,
    scan_source,
)
from rushes.config import ConfigError, load_config
from rushes.context import Context
from rushes.media.probe import probe
from test_clean_v2 import (
    IMAGE_NEEDED,
    REDUCED,
    TINY,
    EditYaml,
    _children,
    _clean,
    _clean_file,
    _close,
    _configure,
    _failures,
    _import,
    _reference,
    _split,
    _warnings,
)


def _refs(config_dir: Path, **themes: tuple[int, int]) -> None:
    """Reference themes under config/refs/<name>.wav: name -> (length in ms, variant)."""
    for name, (ms, variant) in themes.items():
        make_theme(config_dir / "refs" / f"{name}.wav", ms=ms, variant=variant)


def _cleaned(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth, spec: SynthSpec, **kw: Any
) -> tuple[Context, int]:
    """Import one video, clean it, return the context and the episode id."""
    context = _configure(ctx, config_dir, edit_yaml, **kw)
    episode = _import(context, synth(spec))
    assert _clean(context).done == 1, _failures(context)
    return context, episode


# --- the end of the generic snaps to the black after it ---------------------------------------


def test_a_shorter_generic_ends_at_the_black_not_at_the_reference_length(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))  # the reference lasts 12 s, this era's generic 9.5 s
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 9500, 12_000)],
        black_ms=[(9500, 11_000)],
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/long.wav"
    )
    clean = _clean_file(context, episode)
    assert [kind for *_, kind in clean.removed] == ["intro"]
    assert _close(clean.kept[0], 11_000)  # the black's end, not 12 s


def test_a_longer_generic_ends_at_the_black_after_the_reference_length(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, short=(8000, 0))  # the reference lasts 8 s, this era's generic 11 s
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 11_000, 8000)],
        black_ms=[(11_000, 12_500)],
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/short.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 12_500)


def test_a_variant_whose_last_15_percent_differs_still_matches_and_snaps(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, theme=(12_000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 12_000, 12_000, alt_tail_ms=1800)],  # 15 % of it is other music
        black_ms=[(12_000, 13_500)],
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/theme.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 13_500)


def test_snapping_off_keeps_the_nominal_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 9500, 12_000)],
        black_ms=[(9500, 11_000)],
    )
    context, episode = _cleaned(
        ctx,
        config_dir,
        edit_yaml,
        synth,
        spec,
        theme_audio="refs/long.wav",
        clean={"theme_end_snap_s": 0},
    )
    assert _close(_clean_file(context, episode).kept[0], 12_000)


def test_no_black_or_silence_near_the_end_keeps_the_nominal_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, theme=(6000, 0))
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0])
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/theme.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 6000)


# --- several variants --------------------------------------------------------------------------


def test_two_variants_in_a_list_each_remove_their_own_episode(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, old=(6000, 0), new=(6000, 1))
    context = _configure(ctx, config_dir, edit_yaml, theme_audio=["refs/old.wav", "refs/new.wav"])
    spec_a = SynthSpec(duration_ms=20_000, size=TINY, fps=10, generics=[Generic(0, variant=0)])
    spec_b = SynthSpec(duration_ms=21_000, size=TINY, fps=10, generics=[Generic(0, variant=1)])
    first, second = _import(context, synth(spec_a)), _import(context, synth(spec_b))
    assert _clean(context).done == 2, _failures(context)
    for episode in (first, second):
        clean = _clean_file(context, episode)
        assert [kind for *_, kind in clean.removed] == ["intro"]
        assert _close(clean.kept[0], 6000)
    assert _warnings(context) == []


def test_an_unusable_variant_in_a_list_warns_once_and_the_other_still_works(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, good=(6000, 0))
    (config_dir / "refs" / "bad.wav").write_bytes(b"this is not audio")
    context = _configure(ctx, config_dir, edit_yaml, theme_audio=["refs/bad.wav", "refs/good.wav"])
    spec_a = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[0])
    spec_b = SynthSpec(duration_ms=21_000, size=TINY, fps=10, theme_ms=[0])
    first, second = _import(context, synth(spec_a)), _import(context, synth(spec_b))
    assert _clean(context).done == 2, _failures(context)
    assert len(_warnings(context)) == 1 and "bad.wav" in _warnings(context)[0]
    for episode in (first, second):
        assert _close(_clean_file(context, episode).kept[0], 6000)


def test_hits_of_two_variants_on_the_same_generic_are_one_peak() -> None:
    old = ThemePeak(1000, 6000, 0.9, variant=0)
    new = ThemePeak(1500, 8000, 0.7, variant=1)
    assert _one_peak_per_place([old, new]) == [old]  # the strongest, with its length
    assert _one_peak_per_place([new, old]) == [old]
    apart = ThemePeak(20_000, 8000, 0.7, variant=1)
    assert _one_peak_per_place([old, apart]) == [old, apart]
    # two hits of one variant never merge, even a few ms short of a whole theme apart
    near = ThemePeak(6950, 6000, 0.8, variant=0)
    assert _one_peak_per_place([old, near]) == [old, near]


def test_near_equal_hits_of_a_short_and_an_extended_variant_keep_the_longer() -> None:
    # the first 30 s of a 48 s generic match the same place almost as well as the whole one
    full = ThemePeak(1100, 48_000, 0.999101, variant=0)
    prefix = ThemePeak(1100, 30_000, 0.999118, variant=1)  # a hair stronger, by noise
    assert _one_peak_per_place([full, prefix]) == [full]
    assert _one_peak_per_place([prefix, full]) == [full]
    clearly = ThemePeak(1100, 30_000, 0.99, variant=1)  # 0.02 or more apart: the strongest wins
    assert _one_peak_per_place([ThemePeak(1100, 48_000, 0.95, variant=0), clearly]) == [clearly]


# --- where a generic may end -----------------------------------------------------------------


def test_zone_candidates_use_fixed_minimums_and_merge_overlaps() -> None:
    blacks = [(0, 400), (10_000, 11_000), (20_000, 21_000)]  # 400 ms is not a black worth seeing
    silences = [(10_500, 12_500), (30_000, 31_900), (40_000, 42_000)]  # 1.9 s is too short
    assert _zone_candidates(blacks, silences) == [
        (10_000, 11_000, 0),  # black + silence: bounded by the black
        (20_000, 21_000, 1),  # black alone
        (40_000, 42_000, 2),  # silence alone
    ]


def _never(_zone_end: int) -> bool:
    return False


def _always(_zone_end: int) -> bool:
    return True


def _snap(
    zones: list[tuple[int, int, int]],
    nominal: int = 12_000,
    *,
    limit: int = 60_000,
    snap_ms: int = 10_000,
    continues: Callable[[int], bool] = _never,
) -> int:
    """`_snapped_end` for a generic that starts at 0 and nominally lasts `nominal` ms."""
    return _snapped_end(0, nominal, limit, zones, snap_ms, length_ms=nominal, continues=continues)


def test_a_black_or_silence_at_the_start_is_never_the_end_of_a_short_generic() -> None:
    # Oggy and T&J start on a short black: it cannot end a generic that has not begun
    assert _snap([(0, 1000, 1)], 6000) == 6000
    assert _snap([(0, 3000, 1)], 6000) == 6000


def test_a_content_pause_after_a_shorter_generic_is_not_removed() -> None:
    # the reference lasts 48 s, this era's generic 40 s and ends on a black; a pause in the
    # content at 49 s must not become the end
    assert _snap([(39_500, 40_500, 1), (49_000, 49_600, 1)], 48_000) == 40_500


def test_an_internal_fade_where_the_theme_goes_on_is_skipped() -> None:
    # a 55 s generic matched by a 48 s reference, fade at 40.8 s (Oggy's own), real end at 56 s
    zones = [(40_800, 42_140, 0), (55_000, 56_000, 1)]
    assert _snap(zones, 48_000, continues=_always) == 56_000


def test_the_oggy_generic_ends_at_the_black_that_contains_its_nominal_end() -> None:
    zones = [(40_800, 42_140, 0), (47_960, 49_080, 1)]  # the measured blacks of oggy_ep01
    assert _snap(zones, 48_000, continues=_always) == 49_080
    tom = [(5422, 6173, 1), (24_024, 25_484, 1)]  # tom_and_jerry_ep01, 24 s theme
    assert _snap(tom, 24_000, continues=_always) == 25_484


def test_zone_choice_after_the_nominal_end_is_nearest_then_rank() -> None:
    assert _snap([(13_000, 13_500, 1), (16_000, 17_000, 0)], continues=_always) == 13_500
    tie = [(14_000, 14_500, 1), (14_000, 15_500, 0)]  # the same distance: black + silence first
    assert _snap(tie, continues=_always) == 15_500


def test_a_zone_outside_the_window_or_before_the_generic_is_not_an_end() -> None:
    assert _snap([(30_000, 31_000, 1)]) == 12_000  # far
    assert _snap([(22_001, 23_000, 1)]) == 12_000
    assert _snap([(21_999, 23_000, 1)]) == 23_000
    assert _snap([(1000, 2000, 1)]) == 12_000  # before the generic is half over
    assert _snap([(9500, 11_000, 1)], snap_ms=0) == 12_000  # snapping off


def test_a_generic_never_ends_past_the_next_peak_or_the_episode_end() -> None:
    assert _snap([(11_500, 13_000, 1)], limit=11_000, continues=_always) == 11_000
    assert _snap([(11_500, 30_000, 1)], limit=12_500, continues=_always) == 12_500


def test_a_leading_black_does_not_hide_a_short_generic_end_to_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, theme=(8000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 8000, 8000)],
        black_ms=[(0, 1000)],
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/theme.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 8000)  # the whole generic is gone


def test_a_pause_after_a_shorter_generic_stays_in_the_episode_end_to_end(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None], synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 9500, 12_000)],
        black_ms=[(9500, 11_000), (13_000, 14_000)],  # the end of the generic, then a content black
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/long.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 11_000)


def test_a_zone_a_second_before_the_nominal_end_counts_as_the_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # under 1.5 s of the theme would be left after the black: no evidence that it goes on
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 11_000, 12_000)],
        black_ms=[(10_500, 11_500)],
    )
    context, episode = _cleaned(
        ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/long.wav"
    )
    assert _close(_clean_file(context, episode).kept[0], 11_500)


def test_a_child_gives_the_same_removal_inherited_or_scanned_alone(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # a 1 s silence near the nominal end: only silences of 2 s or more count, in both paths
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=45_000,
        size=TINY,
        fps=10,
        generics=[Generic(at, 9500, 12_000) for at in (0, 15_000, 30_000)],
        black_ms=[(at + 9500, at + 11_000) for at in (0, 15_000, 30_000)],
        silence_ms=[(at + 11_500, at + 12_500) for at in (0, 15_000, 30_000)],
    )
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/long.wav", clean=REDUCED)
    video = synth(spec)
    info = probe(video)
    series = context.config.series_by_id(SERIES)
    whole = scan_source(
        context, video, series, info, silence_min_ms=500, black_min_ms=500, themes=ThemeLoader()
    )
    assert len(whole.themes) == 3
    for child in ((0, 15_000), (15_000, 30_000), (30_000, 45_000)):
        inherited = cartoon_removed(context, video, SERIES, info, span=child, scan=whole)
        alone = cartoon_removed(context, video, SERIES, info, span=child, scan=None)
        assert [kind for *_, kind in inherited] == [kind for *_, kind in alone] == ["intro"]
        assert _close(inherited[0][1], child[0] + 11_000) and _close(alone[0][1], child[0] + 11_000)


# --- a closing generic of another length ------------------------------------------------------


def _outro_removed(
    context: Context,
    video: Path,
    span: tuple[int, int] | None = None,
    inherited: bool = False,
) -> list[tuple[int, int, str]]:
    info = probe(video)
    scan = None
    if inherited:
        series = context.config.series_by_id(SERIES)
        scan = scan_source(
            context, video, series, info, silence_min_ms=500, black_min_ms=500, themes=ThemeLoader()
        )
    return list(cartoon_removed(context, video, SERIES, info, span=span, scan=scan))


def test_a_longer_closing_generic_is_removed_to_the_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, short=(8000, 0))  # the reference lasts 8 s, this era's closing 11 s
    spec = SynthSpec(
        duration_ms=30_000, size=TINY, fps=10, generics=[Generic(19_000, 11_000, 8000)]
    )
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/short.wav")
    (zone,) = _outro_removed(context, synth(spec))
    assert zone[2] == "outro" and _close(zone[0], 19_000) and zone[1] == 30_000


def test_a_shorter_closing_generic_cut_by_the_end_of_the_file_is_removed(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))  # the reference lasts 12 s, only 9.5 s play before the end
    spec = SynthSpec(
        duration_ms=30_000, size=TINY, fps=10, generics=[Generic(20_500, 9500, 12_000)]
    )
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/long.wav")
    (zone,) = _outro_removed(context, synth(spec))
    assert zone[2] == "outro" and _close(zone[0], 20_500) and zone[1] == 30_000


def test_an_exact_length_closing_generic_is_unchanged(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, theme=(6000, 0))
    spec = SynthSpec(duration_ms=20_000, size=TINY, fps=10, theme_ms=[14_000])
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/theme.wav")
    assert [(a, kind) for a, _, kind in _outro_removed(context, synth(spec))] == [(14_000, "outro")]


def test_a_theme_in_the_middle_is_not_a_closing_generic(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, short=(8000, 0))
    spec = SynthSpec(
        duration_ms=40_000, size=TINY, fps=10, generics=[Generic(12_000, 11_000, 8000)]
    )
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/short.wav")
    assert _outro_removed(context, synth(spec)) == []


@pytest.mark.parametrize("inherited", [True, False])
def test_closing_generics_of_other_lengths_in_a_child_alone_or_inheriting(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth, inherited: bool
) -> None:
    _refs(config_dir, short=(8000, 0), long=(12_000, 1))
    spec = SynthSpec(
        duration_ms=60_000,
        size=TINY,
        fps=10,
        generics=[
            Generic(38_000, 11_000, 8000, variant=0),  # longer: child (20 s, 50 s) ends at 50 s
            Generic(10_500, 9500, 12_000, variant=1),  # shorter, ends the child (0 s, 20 s)
        ],
    )
    context = _configure(
        ctx, config_dir, edit_yaml, theme_audio=["refs/short.wav", "refs/long.wav"]
    )
    video = synth(spec)
    (late,) = _outro_removed(context, video, span=(20_000, 50_000), inherited=inherited)
    assert late[2] == "outro" and _close(late[0], 38_000) and late[1] == 50_000
    (early,) = _outro_removed(context, video, span=(0, 20_000), inherited=inherited)
    assert early[2] == "outro" and _close(early[0], 10_500) and early[1] == 20_000


# --- reference images and strong audio matches -----------------------------------------------

SHOTS = SynthSpec(duration_ms=24_000, size=TINY, fps=10, shots_ms=[18_000, 22_000])


def _with(**changes: Any) -> SynthSpec:
    return SynthSpec(**{**SHOTS.__dict__, **changes})


def test_a_strong_audio_match_stands_without_any_image_match(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, short=(8000, 0))
    spec = _with(generics=[Generic(0, 11_000, 8000)], black_ms=[(11_000, 12_500)])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=20)  # never within 17 s of the start
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/short.wav", references=[ref])
    episode = _import(context, video)
    assert _clean(context).done == 1
    assert _close(_clean_file(context, episode).kept[0], 12_500)  # a match above 0.85 stands


def test_a_weak_audio_match_without_an_image_match_is_dropped(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = _with(generics=[Generic(0, 9500, 12_000)], black_ms=[(9500, 11_000)])  # corr about 0.74
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=20)
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/long.wav", references=[ref])
    episode = _import(context, video)
    assert _clean(context).done == 1
    assert _clean_file(context, episode).removed == []


def test_a_weak_audio_match_stands_when_the_sure_threshold_is_lowered(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = _with(generics=[Generic(0, 9500, 12_000)], black_ms=[(9500, 11_000)])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=20)
    context = _configure(
        ctx,
        config_dir,
        edit_yaml,
        theme_audio="refs/long.wav",
        references=[ref],
        clean={"theme_sure_threshold": 0.7},
    )
    episode = _import(context, video)
    assert _clean(context).done == 1
    assert _close(_clean_file(context, episode).kept[0], 11_000)


def test_an_image_near_a_weak_audio_match_confirms_it(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = _with(generics=[Generic(0, 9500, 12_000)], black_ms=[(9500, 11_000)])
    video = synth(spec)
    ref = _reference(config_dir, video, at_s=3)  # the opening shot, under the generic
    context = _configure(
        ctx,
        config_dir,
        edit_yaml,
        theme_audio="refs/long.wav",
        references=[ref],
        clean=IMAGE_NEEDED,
    )
    episode = _import(context, video)
    assert _clean(context).done == 1
    assert _clean_file(context, episode).removed[0][2] == "intro"


# --- idempotence and children of a split ------------------------------------------------------


def test_r16_snapped_generic_rerun_changes_nothing(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 9500, 12_000)],
        black_ms=[(9500, 11_000)],
    )
    context, _ = _cleaned(ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/long.wav")
    before = snapshot(context)
    report = _clean(context)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(context) == before


def test_children_of_a_split_snap_each_generic_to_its_own_black(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    _refs(config_dir, long=(12_000, 0))
    spec = SynthSpec(
        duration_ms=45_000,
        size=TINY,
        fps=10,
        generics=[Generic(at, 9500, 12_000) for at in (0, 15_000, 30_000)],
        black_ms=[(at + 9500, at + 11_000) for at in (0, 15_000, 30_000)],
    )
    context, parent = _split(ctx, config_dir, edit_yaml, synth, spec, theme_audio="refs/long.wav")
    kids = _children(context, parent)
    assert len(kids) == 3
    for kid in kids:
        clean = _clean_file(context, kid["id"])
        assert [kind for *_, kind in clean.removed] == ["intro"]
        assert _close(clean.kept[0], kid["span_start_ms"] + 11_000)
        assert clean.kept[1] == kid["span_end_ms"]


# --- settings and config ----------------------------------------------------------------------


def test_theme_audio_as_a_string_or_a_list_both_load(config_dir: Path, edit_yaml: EditYaml) -> None:
    _refs(config_dir, a=(6000, 0), b=(6000, 1))
    path = config_dir / "series" / f"{SERIES}.yaml"
    edit_yaml(path, lambda d: d.update(intro_outro={"theme_audio": "refs/a.wav"}))
    one = load_config(config_dir).series_by_id(SERIES)
    assert one is not None and one.intro_outro.theme_paths == ["refs/a.wav"]
    edit_yaml(path, lambda d: d.update(intro_outro={"theme_audio": ["refs/a.wav", "refs/b.wav"]}))
    two = load_config(config_dir).series_by_id(SERIES)
    assert two is not None and two.intro_outro.theme_paths == ["refs/a.wav", "refs/b.wav"]
    edit_yaml(path, lambda d: d.update(intro_outro={}))
    none = load_config(config_dir).series_by_id(SERIES)
    assert none is not None and none.intro_outro.theme_paths == []


def test_a_missing_variant_in_the_list_is_refused_with_its_name(
    config_dir: Path, edit_yaml: EditYaml
) -> None:
    _refs(config_dir, a=(6000, 0))
    path = config_dir / "series" / f"{SERIES}.yaml"
    listed = ["refs/a.wav", "refs/absent.wav"]
    edit_yaml(path, lambda d: d.update(intro_outro={"theme_audio": listed}))
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)
    message = str(excinfo.value)
    assert "intro_outro.theme_audio.1" in message and "refs/absent.wav" in message
    assert "refs/a.wav" not in message  # the existing one is not blamed


def test_an_empty_theme_list_is_refused(config_dir: Path, edit_yaml: EditYaml) -> None:
    path = config_dir / "series" / f"{SERIES}.yaml"
    edit_yaml(path, lambda d: d.update(intro_outro={"theme_audio": []}))
    with pytest.raises(ConfigError):
        load_config(config_dir)


@pytest.mark.parametrize(
    "bad",
    [
        {"theme_end_snap_s": -1},
        {"theme_end_snap_s": 31},
        {"theme_sure_threshold": 0.59},  # under theme_threshold
        {"theme_sure_threshold": 1.01},
        {"theme_threshold": 0.9, "theme_sure_threshold": 0.85},
    ],
)
def test_bad_snap_and_sure_values_are_refused(
    config_dir: Path, edit_yaml: EditYaml, bad: Any
) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean=bad))
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)
    assert "clean" in str(excinfo.value)


def test_snap_and_sure_values_in_range_load(config_dir: Path, edit_yaml: EditYaml) -> None:
    for snap, sure in ((0, 0.6), (30, 1.0), (10, 0.85)):
        values = {"theme_end_snap_s": snap, "theme_sure_threshold": sure}
        edit_yaml(config_dir / "settings.yaml", lambda d, v=values: d.update(clean=v))
        loaded = load_config(config_dir).settings.clean
        assert (loaded.theme_end_snap_s, loaded.theme_sure_threshold) == (snap, sure)


def test_the_sure_threshold_defaults_to_085_or_the_match_threshold_if_higher(
    config_dir: Path, edit_yaml: EditYaml
) -> None:
    assert load_config(config_dir).settings.clean.sure_threshold == 0.85
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean={"theme_threshold": 0.9}))
    stricter = load_config(config_dir).settings.clean  # no second key needed
    assert stricter.theme_sure_threshold is None and stricter.sure_threshold == 0.9
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(clean={"theme_sure_threshold": 0.7}))
    assert load_config(config_dir).settings.clean.sure_threshold == 0.7


@pytest.mark.parametrize("bad", [42, {"a": 1}, ["refs/a.wav", 3], "", ["refs/a.wav", "refs/a.wav"]])
def test_theme_audio_of_the_wrong_kind_is_refused_in_french(
    config_dir: Path, edit_yaml: EditYaml, bad: Any
) -> None:
    _refs(config_dir, a=(6000, 0))
    path = config_dir / "series" / f"{SERIES}.yaml"
    edit_yaml(path, lambda d: d.update(intro_outro={"theme_audio": bad}))
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)
    message = str(excinfo.value)
    assert "theme_audio" in message and "Input should be" not in message
    if isinstance(bad, list) and "refs/a.wav" in bad and len(bad) == 2 and bad[1] == "refs/a.wav":
        assert "en double" in message
    elif bad != "":
        assert "un fichier ou une liste de fichiers" in message


# --- the real "does the theme go on" test, no stubs --------------------------------------------


def _generic_end(
    ctx: Context,
    config_dir: Path,
    edit_yaml: EditYaml,
    synth: Synth,
    spec: SynthSpec,
    *,
    ref_ms: int,
    span: tuple[int, int] | None = None,
    inherited: bool = False,
) -> list[tuple[int, int, str]]:
    _refs(config_dir, ref=(ref_ms, 0))
    context = _configure(ctx, config_dir, edit_yaml, theme_audio="refs/ref.wav")
    return _outro_removed(context, synth(spec), span=span, inherited=inherited)


def test_an_internal_fade_where_the_theme_goes_on_is_skipped_end_to_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # 16 s generic, 12 s reference: a fade at 8 s with the theme going on, the real end at 16-17 s
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 16_000, 12_000)],
        black_ms=[(8000, 8600), (16_000, 17_000)],
    )
    (zone,) = _generic_end(ctx, config_dir, edit_yaml, synth, spec, ref_ms=12_000)
    assert zone[2] == "intro" and zone[0] == 0 and _close(zone[1], 17_000)


def test_a_black_after_which_the_theme_stops_ends_the_generic_end_to_end(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # 9 s generic, 12 s reference: the black at 9-10 s ends it (2 s of reference are left, no match)
    spec = SynthSpec(
        duration_ms=30_000,
        size=TINY,
        fps=10,
        generics=[Generic(0, 9000, 12_000)],
        black_ms=[(9000, 10_000)],
    )
    (zone,) = _generic_end(ctx, config_dir, edit_yaml, synth, spec, ref_ms=12_000)
    assert zone[2] == "intro" and _close(zone[1], 10_000)


@pytest.mark.parametrize("inherited", [True, False])
def test_the_fade_test_in_a_child_span_whose_start_is_not_zero(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth, inherited: bool
) -> None:
    # the same two generics in the child (20 s, 80 s): frames are counted from the child's start
    # when it is scanned alone and from the start of the file when it inherits the scan
    spec = SynthSpec(
        duration_ms=100_000,
        size=TINY,
        fps=10,
        generics=[Generic(20_000, 16_000, 12_000), Generic(60_000, 9000, 12_000)],
        black_ms=[(28_000, 28_600), (36_000, 37_000), (69_000, 70_000)],
    )
    kw = {"ref_ms": 12_000, "inherited": inherited}
    first = _generic_end(ctx, config_dir, edit_yaml, synth, spec, span=(20_000, 50_000), **kw)
    assert [(a, kind) for a, _, kind in first] == [(20_000, "intro")]
    assert _close(first[0][1], 37_000)  # the fade at 28 s is skipped, the real end is 37 s
    second = _generic_end(ctx, config_dir, edit_yaml, synth, spec, span=(60_000, 100_000), **kw)
    assert [(a, kind) for a, _, kind in second] == [(60_000, "intro")]
    assert _close(second[0][1], 70_000)  # the black at 69-70 s ends the shorter generic


# --- a closing generic that is the opening's theme, with a cold open after it -----------------


def test_each_child_starts_at_its_opening_and_the_closing_and_teaser_are_removed(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, synth: Synth
) -> None:
    # three episodes: opening theme, story, the same theme as closing credits, a 3 s teaser
    # before the next opening (the last episode's closing is the end of the file)
    spec = SynthSpec(
        duration_ms=66_000,
        size=TINY,
        fps=10,
        theme_ms=[0, 14_000, 23_000, 37_000, 46_000, 60_000],
    )
    context, parent = _split(ctx, config_dir, edit_yaml, synth, spec, theme=True)
    kids = _children(context, parent)
    spans = [(k["span_start_ms"], k["span_end_ms"]) for k in kids]
    assert len(spans) == 3  # no bogus child
    # each child starts at an opening (the cut is at the later of closing and opening)
    assert spans[0][0] == 0 and _close(spans[0][1], 23_000, 100)
    assert _close(spans[1][0], 23_000, 100) and _close(spans[1][1], 46_000, 100)
    assert _close(spans[2][0], 46_000, 100)
    first, second, third = (_clean_file(context, k["id"]) for k in kids)
    # opening removed as intro; closing credits and the teaser after them as outro
    assert [kind for *_, kind in first.removed] == ["intro", "outro"]
    assert _close(first.kept[0], 6000) and _close(first.kept[1], 14_000)
    assert [kind for *_, kind in second.removed] == ["intro", "outro"]
    assert _close(second.kept[0], 29_000) and _close(second.kept[1], 37_000)
    # the last closing (60 s) is a peak 14 s after the opening: it is a cut, and the piece of
    # closing credits left is shorter than an episode and dropped
    assert [kind for *_, kind in third.removed] == ["intro"]
    assert _close(third.kept[0], 52_000) and _close(third.kept[1], 60_000, 100)
    assert _close(spans[2][1], 60_000, 100)
