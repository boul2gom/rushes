"""Analysis, sounds (task 12b): CED labels grouped into events, audio.json, scenes."""

import csv
import dataclasses
import os
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.orm import Session

from fakes.models import FakeSoundTagger, FakeVad
from fixtures.generator import Synth, SynthSpec
from helpers import cleaned_episode, ctx_events, make_episode, put_clean, snapshot
from rushes.analyze.analyze import AnalyzeStep
from rushes.analyze.facets import facets_done
from rushes.analyze.sounds import AUDIOSET_GROUPS, SoundsStep, group_events
from rushes.context import Context, Filters
from rushes.core.types import SoundEvent
from rushes.db.models import Scene
from rushes.errors import MediaError
from rushes.ids import episode_key
from rushes.media.audio import decode_pcm16k
from rushes.models.ced import CedTagger
from rushes.models.hub import ModelHub
from rushes.runner import StepReport, run_step
from rushes.schemas import AudioFile, LoudPoint, read_json, write_json

TINY = (64, 36)
SCENES = [(0, 2000), (2000, 6000), (6000, 8000)]
BEEP = SynthSpec(duration_ms=8000, size=TINY, fps=10, beeps_ms=[(4000, 5000)])  # in scene 1
TWO_BEEPS = SynthSpec(duration_ms=8000, size=TINY, fps=10, beeps_ms=[(3000, 3500), (4500, 5000)])
QUIET = SynthSpec(duration_ms=8000, size=TINY, fps=10)
NO_AUDIO = SynthSpec(duration_ms=8000, size=TINY, fps=10, audio=False)


# --- helpers ----------------------------------------------------------------------------------


def _folder(ctx: Context, episode_id: int) -> Path:
    return ctx.paths.episode_dir(episode_key(episode_id))


def _episode(ctx: Context, video: Path, spans: Sequence[tuple[int, int]] = SCENES) -> int:
    """An analyzed episode: clean.mp4, a scene per span, an audio.json whose events are None."""
    with Session(ctx.engine) as session:
        episode = make_episode(session)
        put_clean(ctx, episode, video)
        for start, end in spans:
            session.add(Scene(episode_id=episode.id, start_ms=start, end_ms=end))
        session.commit()
        episode_id = episode.id
    write_json(
        AudioFile(
            version=1,
            episode_id=episode_id,
            integrated_lufs=-21.5,
            loudness=[LoudPoint(t_ms=3000, lufs=-22.0), LoudPoint(t_ms=6000, lufs=-19.5)],
            speech=[(100, 900), (5000, 5600)],
        ),
        _folder(ctx, episode_id) / "audio.json",
    )
    return episode_id


def _sounds(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, SoundsStep(), Filters(**filters))


def _with_tagger(ctx: Context, tagger: Any) -> Context:
    """The same context whose sound tagger is `tagger`."""
    hub = ModelHub(
        ctx.paths, ctx.config.settings, factories={"vad": FakeVad, "sounds": lambda: tagger}
    )
    return dataclasses.replace(ctx, models=hub)


def _scene_json(ctx: Context, episode_id: int) -> list[str | None]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT sound_events_json FROM scenes WHERE episode_id=:e ORDER BY start_ms"),
            {"e": episode_id},
        )
        return [row[0] for row in rows]


def _done(ctx: Context, episode_id: int) -> set[str]:
    with Session(ctx.engine) as session:
        return set(facets_done(session, ctx.paths, episode_id))


def _events(ctx: Context, episode_id: int) -> list[SoundEvent] | None:
    return read_json(AudioFile, _folder(ctx, episode_id) / "audio.json").events


class ScriptedTagger:
    """Gives the events it was built with, whatever the audio."""

    def __init__(self, events: Sequence[SoundEvent]) -> None:
        self.events = list(events)

    def tag(self, pcm16k: NDArray[np.float32]) -> list[SoundEvent]:
        return list(self.events)


# --- grouping ---------------------------------------------------------------------------------


def test_audioset_groups_are_the_brief_names() -> None:
    expected = {
        "impact": {
            "Thump, thud",
            "Bang",
            "Slap, smack",
            "Whack, thwack",
            "Smash, crash",
            "Knock",
            "Crushing",
            "Boing",
        },
        "explosion": {"Explosion", "Boom", "Gunshot, gunfire", "Fireworks"},
        "splash": {"Splash, splatter", "Slosh", "Pour"},
        "scream": {"Screaming", "Yell", "Shout", "Children shouting"},
        "laughter": {
            "Laughter",
            "Giggle",
            "Chuckle, chortle",
            "Belly laugh",
            "Snicker",
            "Baby laughter",
        },
    }
    found: dict[str, set[str]] = {}
    for name, group in AUDIOSET_GROUPS.items():
        found.setdefault(group, set()).add(name)
    assert found == expected


def test_group_events_merges_and_thresholds() -> None:
    windows = [
        (3500, [("Laughter", 0.35)]),
        (500, [("Bang", 0.4)]),  # unsorted on purpose
        (0, [("Bang", 0.5), ("Thump, thud", 0.7), ("Speech", 0.95)]),  # best of the group: 0.7
        (1000, [("Bang", 0.29)]),  # under min_score: not a hit
        (3000, [("Bang", 0.6), ("Knock", 0.2)]),
        (9000, [("Bang", 0.3)]),  # exactly min_score: kept
    ]
    assert group_events(windows) == [
        SoundEvent(0, 1500, "impact", 0.7),  # windows 0 and 500 overlap: one event, best score
        SoundEvent(3000, 4000, "impact", 0.6),  # 1500 ms after the previous one: apart
        SoundEvent(3500, 4500, "laughter", 0.35),  # its own group, overlapping another one
        SoundEvent(9000, 10_000, "impact", 0.3),
    ]


def test_group_events_gap_is_inclusive_and_follows_the_window_length() -> None:
    def two(second: int, **kwargs: int) -> list[SoundEvent]:
        return group_events([(0, [("Boom", 0.9)]), (second, [("Boom", 0.5)])], **kwargs)

    assert two(1500) == [SoundEvent(0, 2500, "explosion", 0.9)]  # gap 500 = gap_ms: merged
    assert [e.end_ms for e in two(1501)] == [1000, 2501]  # gap 501: two events
    assert two(1501, gap_ms=501) == [SoundEvent(0, 2501, "explosion", 0.9)]
    assert two(1000, window_ms=500, gap_ms=499) == [
        SoundEvent(0, 500, "explosion", 0.9),
        SoundEvent(1000, 1500, "explosion", 0.5),
    ]
    assert group_events([(0, [("Boom", 0.9)])], window_ms=250) == [
        SoundEvent(0, 250, "explosion", 0.9)
    ]


def test_group_events_nothing_found_is_an_empty_list() -> None:
    assert group_events([]) == []
    assert group_events([(0, [("Bang", 0.1)])]) == []


def test_unmapped_labels_ignored() -> None:
    windows = [(0, [("Speech", 0.99), ("Music", 0.9), ("bang", 0.9), ("Silence", 0.8)])]
    assert group_events(windows) == []  # unknown names, and the case of a known one matters
    assert group_events([(0, [("Speech", 0.99), ("Splash, splatter", 0.4)])]) == [
        SoundEvent(0, 1000, "splash", 0.4)
    ]


# --- the step ---------------------------------------------------------------------------------


def test_sounds_step_fills_events_and_scenes(ctx: Context, synth: Synth) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    assert "sounds" not in _done(ctx, episode_id)
    report = _sounds(ctx)
    assert (report.done, report.failed) == (1, 0), ctx_events(ctx)
    events = _events(ctx, episode_id)
    assert events is not None and len(events) == 1
    assert events[0].label == "impact"
    assert abs(events[0].start_ms - 4000) <= 200 and abs(events[0].end_ms - 5000) <= 200
    assert _scene_json(ctx, episode_id) == ["{}", '{"impact": 1}', "{}"]
    assert "sounds" in _done(ctx, episode_id)


def test_sounds_step_chains_after_analyze_on_the_time_of_clean_mp4(
    ctx: Context, synth: Synth
) -> None:
    video = synth(
        SynthSpec(
            duration_ms=8000, size=TINY, fps=10, shots_ms=[2000, 6000], beeps_ms=[(4000, 5000)]
        )
    )
    episode_id = cleaned_episode(ctx, video)
    assert run_step(ctx, AnalyzeStep(), Filters()).done == 1
    assert _sounds(ctx).done == 1
    with ctx.engine.connect() as conn:  # the detector may cut inside a shot: read the real scenes
        spans = [
            tuple(r)
            for r in conn.execute(text("SELECT start_ms, end_ms FROM scenes ORDER BY start_ms"))
        ]
    assert spans[0] == (0, 2000) and spans[-1] == (6000, 8000)
    expected = ['{"impact": 1}' if a < 5000 and b > 4000 else "{}" for a, b in spans]
    assert expected.count("{}") >= 2 and expected.count('{"impact": 1}') >= 1
    assert (
        _scene_json(ctx, episode_id) == expected
    )  # the beep is in clean.mp4 time, like the scenes


def test_sounds_scene_counts_are_per_label_sorted_and_half_open(ctx: Context, synth: Synth) -> None:
    episode_id = _episode(ctx, synth(QUIET), spans=[(0, 1000), (1000, 3000), (3000, 5000)])
    tagger = ScriptedTagger(
        [
            SoundEvent(500, 1000, "scream", 0.9),  # ends where scene 1 starts: not in scene 1
            SoundEvent(900, 1100, "impact", 0.5),  # across scenes 0 and 1: counted in both
            SoundEvent(1200, 1300, "impact", 0.5),
            SoundEvent(1500, 4000, "impact", 0.5),  # across scenes 1 and 2
            SoundEvent(5000, 6000, "splash", 0.5),  # starts where scene 2 ends: in none
        ]
    )
    assert _sounds(_with_tagger(ctx, tagger)).done == 1
    assert _scene_json(ctx, episode_id) == [
        '{"impact": 1, "scream": 1}',
        '{"impact": 3}',
        '{"impact": 1}',
    ]
    events = _events(ctx, episode_id)
    assert events == tagger.events


def test_events_none_means_not_analyzed_empty_means_done(ctx: Context, synth: Synth) -> None:
    quiet = _episode(ctx, synth(QUIET))
    mute = _episode(ctx, synth(NO_AUDIO))
    beeps = _episode(ctx, synth(BEEP))
    assert [_events(ctx, i) for i in (quiet, mute, beeps)] == [None, None, None]
    tagger = FakeSoundTagger()
    report = _sounds(_with_tagger(ctx, tagger))
    assert (report.done, report.failed) == (3, 0), ctx_events(ctx)
    assert _events(ctx, quiet) == [] and _events(ctx, mute) == []  # looked at, nothing there
    assert _events(ctx, beeps) != []
    assert len(tagger.calls) == 2  # a file without an audio stream is not given to the tagger
    for episode_id in (quiet, mute):
        assert _scene_json(ctx, episode_id) == ["{}", "{}", "{}"]
        assert "sounds" in _done(ctx, episode_id)
    assert SoundsStep().select(ctx, Filters()) == []  # [] is done


def test_sounds_keeps_the_other_fields_of_audio_json(ctx: Context, synth: Synth) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    before = read_json(AudioFile, _folder(ctx, episode_id) / "audio.json")
    assert _sounds(ctx).done == 1
    after = read_json(AudioFile, _folder(ctx, episode_id) / "audio.json")
    assert after.events and after.model_copy(update={"events": None}) == before


def test_r16_sounds_rerun_changes_nothing(ctx: Context, synth: Synth) -> None:
    _episode(ctx, synth(BEEP))
    _episode(ctx, synth(QUIET))
    assert _sounds(ctx).done == 2
    before = snapshot(ctx)
    again = _sounds(ctx)
    assert (again.done, again.skipped, again.failed) == (0, 0, 0)  # nothing is selected
    assert snapshot(ctx) == before
    forced = _sounds(ctx, force=True)  # recomputed: the very same rows and bytes
    assert (forced.done, forced.failed) == (2, 0)
    assert snapshot(ctx) == before


def test_r19_sounds_tagger_loads_once_for_two_episodes(ctx: Context, synth: Synth) -> None:
    _episode(ctx, synth(BEEP))
    _episode(ctx, synth(TWO_BEEPS))
    assert _sounds(ctx).done == 2
    assert ctx.models.loads()["sounds"] == 1


def test_sounds_selection_and_filters(ctx: Context, synth: Synth) -> None:
    step = SoundsStep()
    done = _episode(ctx, synth(BEEP))
    todo = _episode(ctx, synth(QUIET))
    with Session(ctx.engine) as session:  # not analyzed: never selected
        make_episode(session).status = "cleaned"
        session.commit()
    assert _sounds(ctx, episode=done).done == 1
    assert step.select(ctx, Filters()) == [todo]
    assert step.select(ctx, Filters(force=True)) == [done, todo]
    assert step.select(ctx, Filters(episode=done)) == []
    assert step.select(ctx, Filters(episode=done, force=True)) == [done]
    assert step.select(ctx, Filters(series="oggy")) == []
    assert step.select(ctx, Filters(creator="squeezie")) == []
    assert step.select(ctx, Filters(account="st_tt")) == [todo]
    assert step.select(ctx, Filters(account="tt_tt")) == []


def test_sounds_without_clean_mp4_fails_the_item_and_writes_nothing(
    ctx: Context, synth: Synth
) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    (_folder(ctx, episode_id) / "clean.mp4").unlink()
    bytes_before = (_folder(ctx, episode_id) / "audio.json").read_bytes()
    with pytest.raises(MediaError, match=r"clean\.mp4") as caught:
        SoundsStep().run(ctx, episode_id, force=False)
    assert str(_folder(ctx, episode_id)) in (caught.value.hint or "")
    report = _sounds(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert (_folder(ctx, episode_id) / "audio.json").read_bytes() == bytes_before
    assert _scene_json(ctx, episode_id) == [None, None, None]


def test_sounds_releases_the_models_when_the_step_ends(ctx: Context, synth: Synth) -> None:
    _episode(ctx, synth(BEEP))
    assert _sounds(ctx).done == 1
    assert ctx.models.loads()["sounds"] == 1
    ctx.models.sounds()  # reloaded: the step let go of it (the run goes on with FFmpeg steps)
    assert ctx.models.loads()["sounds"] == 2


def test_sounds_events_the_file_could_not_read_back_fail_the_item_and_write_nothing(
    ctx: Context, synth: Synth
) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    bad = ScriptedTagger([SoundEvent(4000, 5000, "bogus", 0.9)])  # type: ignore[arg-type]
    before = (_folder(ctx, episode_id) / "audio.json").read_bytes()
    with pytest.raises(MediaError, match="invalides") as caught:
        SoundsStep().run(_with_tagger(ctx, bad), episode_id, force=False)
    assert caught.value.hint
    report = _sounds(_with_tagger(ctx, bad))
    assert (report.done, report.failed) == (0, 1)
    assert (_folder(ctx, episode_id) / "audio.json").read_bytes() == before
    assert _scene_json(ctx, episode_id) == [None, None, None]  # validated before the rows
    assert _sounds(ctx).done == 1  # the facet is still undone: a good run fixes it


def test_sounds_audio_json_of_another_episode_fails_with_a_hint(ctx: Context, synth: Synth) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    path = _folder(ctx, episode_id) / "audio.json"
    write_json(read_json(AudioFile, path).model_copy(update={"episode_id": episode_id + 1}), path)
    with pytest.raises(MediaError, match="appartient") as caught:
        SoundsStep().run(ctx, episode_id, force=False)
    assert str(_folder(ctx, episode_id)) in (caught.value.hint or "")
    assert _scene_json(ctx, episode_id) == [None, None, None]


def test_sounds_without_audio_json_fails_the_item(ctx: Context, synth: Synth) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    (_folder(ctx, episode_id) / "audio.json").unlink()
    with pytest.raises(MediaError, match=r"audio\.json"):
        SoundsStep().run(ctx, episode_id, force=False)
    assert _scene_json(ctx, episode_id) == [None, None, None]


def test_sounds_crash_between_the_rows_and_the_file_is_repaired_by_the_next_run(
    ctx: Context, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_id = _episode(ctx, synth(BEEP))
    real = write_json

    def killed(model: Any, path: Path) -> None:
        raise RuntimeError("killed before the file")

    monkeypatch.setattr("rushes.analyze.sounds.write_json", killed)
    report = _sounds(ctx)
    assert report.failed == 1
    assert _scene_json(ctx, episode_id) == ["{}", '{"impact": 1}', "{}"]  # rows first
    assert _events(ctx, episode_id) is None  # the facet is still not done
    monkeypatch.setattr("rushes.analyze.sounds.write_json", real)
    assert _sounds(ctx).done == 1  # so the next run redoes both
    assert _events(ctx, episode_id) is not None
    assert _scene_json(ctx, episode_id) == ["{}", '{"impact": 1}', "{}"]


# --- the CED tagger (sherpa_onnx replaced by a fake module) --------------------------------------

LABELS = [("0", "/m/0", "Speech"), ("1", "/m/1", "Thump, thud"), ("2", "/m/2", "Laughter")]


def _fake_sherpa(seen: dict[str, Any]) -> types.ModuleType:
    """What the tagger needs of sherpa_onnx: the config classes and an AudioTagging that reads
    the label CSV it is given and calls a window loud (peak above 0.5) a "Thump, thud"."""
    module = types.ModuleType("sherpa_onnx")

    class ModelConfig:
        def __init__(self, ced: str = "", num_threads: int = 1) -> None:
            self.ced, self.num_threads = ced, num_threads

    class Config:
        def __init__(self, model: ModelConfig, labels: str, top_k: int = 5) -> None:
            self.model, self.labels, self.top_k = model, labels, top_k

    class Event:
        def __init__(self, name: str, prob: float) -> None:
            self.name, self.prob = name, prob

    class Stream:
        def __init__(self) -> None:
            self.samples: NDArray[np.float32] = np.zeros(0, dtype=np.float32)

        def accept_waveform(self, sample_rate: int, samples: NDArray[np.float32]) -> None:
            seen["rates"].append(sample_rate)
            self.samples = samples

    class AudioTagging:
        def __init__(self, config: Config) -> None:
            seen["config"] = config
            with Path(config.labels).open(encoding="utf-8", newline="") as handle:
                self.names = [row["display_name"] for row in csv.DictReader(handle)]

        def create_stream(self) -> Stream:
            return Stream()

        def compute(self, stream: Stream, top_k: int = -1) -> list[Event]:
            seen["sizes"].append(len(stream.samples))
            seen["top_k"].append(top_k)
            loud = bool(len(stream.samples)) and float(np.abs(stream.samples).max()) > 0.5
            ranked = [self.names[1], self.names[0], self.names[2]] if loud else self.names
            return [
                Event(name, float(np.float32((0.8, 0.123456, 0.05)[i])))
                for i, name in enumerate(ranked)
            ][:top_k]

    module.AudioTaggingModelConfig = ModelConfig  # type: ignore[attr-defined]
    module.AudioTaggingConfig = Config  # type: ignore[attr-defined]
    module.AudioTagging = AudioTagging  # type: ignore[attr-defined]
    return module


@pytest.fixture
def sherpa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    seen: dict[str, Any] = {"rates": [], "sizes": [], "top_k": []}
    monkeypatch.setitem(sys.modules, "sherpa_onnx", _fake_sherpa(seen))
    folder = tmp_path / "ced"
    folder.mkdir()
    with (folder / "class_labels_indices.csv").open("w", encoding="utf-8", newline="") as handle:
        handle.write("index,mid,display_name\n")
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)  # the real file quotes every name
        writer.writerows(LABELS)
    return folder, seen


def _pcm(seconds: float, loud: tuple[float, float] | None = None) -> NDArray[np.float32]:
    pcm = np.zeros(int(seconds * 16_000), dtype=np.float32)
    if loud is not None:
        pcm[int(loud[0] * 16_000) : int(loud[1] * 16_000)] = 0.9
    return pcm


def test_ced_tagger_windows_hops_labels_and_top_k(sherpa: tuple[Path, dict[str, Any]]) -> None:
    folder, seen = sherpa
    tagger = CedTagger(folder, threads=2, window_ms=1000, hop_ms=500, top_k=2)
    config = seen["config"]
    assert config.model.ced == str(folder / "model.int8.onnx")
    assert (config.model.num_threads, config.labels, config.top_k) == (
        2,
        str(folder / "class_labels_indices.csv"),
        2,
    )
    tags = tagger.window_tags(_pcm(3.2, loud=(1.0, 2.0)))
    # every 500 ms, then a last window that ends with the audio
    assert [start for start, _ in tags] == [0, 500, 1000, 1500, 2000, 2200]
    assert tags[0][1] == [("Speech", 0.8), ("Thump, thud", 0.1235)]  # names as in the CSV, top_k 2
    assert tags[2][1][0][0] == "Thump, thud"
    assert seen["sizes"] == [16_000] * 6 and seen["top_k"] == [2] * 6
    assert set(seen["rates"]) == {16_000}


def test_ced_tagger_rounds_the_scores_to_four_decimals(
    sherpa: tuple[Path, dict[str, Any]],
) -> None:
    folder, _ = sherpa
    tags = CedTagger(folder, top_k=2).window_tags(_pcm(1.0))
    # 0.8 is 0.800000011920929 as a float32, 0.123456 stays 0.1235: four decimals, no fewer
    assert tags[0][1] == [("Speech", 0.8), ("Thump, thud", 0.1235)]


def test_ced_tagger_keeps_events_inside_a_short_audio(
    sherpa: tuple[Path, dict[str, Any]],
) -> None:
    folder, _ = sherpa
    tagger = CedTagger(folder, top_k=2)
    # 400 ms of loud audio: its one window is 1000 ms long, the event stops with the audio
    assert tagger.tag(_pcm(0.4, loud=(0.0, 0.4))) == [SoundEvent(0, 400, "impact", 0.8)]


def test_ced_tagger_drops_an_event_that_starts_after_the_audio_ends(
    sherpa: tuple[Path, dict[str, Any]],
) -> None:
    folder, _ = sherpa
    tagger = CedTagger(folder, top_k=2)
    loud = np.full(
        8, 0.9, dtype=np.float32
    )  # half a millisecond: its window starts at 0 ms = the end
    assert tagger.window_tags(loud)[0][1][0][0] == "Thump, thud"  # heard, then dropped
    assert tagger.tag(loud) == []


def test_ced_tagger_groups_the_windows_into_events(sherpa: tuple[Path, dict[str, Any]]) -> None:
    folder, _ = sherpa
    tagger = CedTagger(folder, top_k=2)
    # windows 500, 1000 and 1500 hear the loud second; "Thump, thud" is an impact
    assert tagger.tag(_pcm(3.2, loud=(1.0, 2.0))) == [SoundEvent(500, 2500, "impact", 0.8)]
    assert tagger.tag(_pcm(3.2)) == []  # Speech only: no event


def test_ced_tagger_short_and_empty_audio(sherpa: tuple[Path, dict[str, Any]]) -> None:
    folder, seen = sherpa
    tagger = CedTagger(folder)
    assert tagger.window_tags(_pcm(0)) == []
    assert seen["sizes"] == []  # nothing to tag
    short = tagger.window_tags(_pcm(0.4))
    assert [start for start, _ in short] == [0] and seen["sizes"] == [6400]


def test_ced_tagger_windows_follow_the_constructor(sherpa: tuple[Path, dict[str, Any]]) -> None:
    folder, seen = sherpa
    tagger = CedTagger(folder, window_ms=500, hop_ms=250, top_k=3)
    assert [s for s, _ in tagger.window_tags(_pcm(1.0))] == [0, 250, 500]
    assert seen["sizes"] == [8000] * 3
    with pytest.raises(ValueError, match="au moins 1"):
        CedTagger(folder, hop_ms=0)


@pytest.mark.slow
def test_ced_runs_on_synthetic_audio(tmp_path: Path, synth: Synth) -> None:
    """The real CED-mini; opt-in: RUSHES_TEST_CED_DIR holds model.int8.onnx and the labels CSV."""
    directory = os.environ.get("RUSHES_TEST_CED_DIR")
    if not directory:
        pytest.skip("RUSHES_TEST_CED_DIR (dossier du modèle CED) non défini")
    with (Path(directory) / "class_labels_indices.csv").open(encoding="utf-8", newline="") as h:
        names = {row["display_name"] for row in csv.DictReader(h)}
    assert set(AUDIOSET_GROUPS) <= names  # our 25 names are spelled as the file spells them
    pcm = decode_pcm16k(synth(BEEP), tmp_dir=tmp_path)
    tagger = CedTagger(Path(directory))
    tags = tagger.window_tags(pcm)
    heard = {name for _, labels in tags for name, _ in labels}
    assert heard and heard <= names  # what sherpa returns is a name of the file: no quote, no \r
    events = tagger.tag(pcm)
    assert isinstance(events, list)
    assert all(isinstance(event, SoundEvent) for event in events)
    assert "laughter" not in {event.label for event in events}  # a beep is not a laugh
    assert all(0 <= e.start_ms < e.end_ms and 0.0 <= e.score <= 1.0 for e in events)
