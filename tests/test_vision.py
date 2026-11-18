"""Analysis, vision (task 12a): facets, CLIP tags, characters, vectors, free-text search."""

import dataclasses
import json
import math
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from sqlalchemy.orm import Session

from fakes.models import (
    LABEL_AXES,
    NO_LABEL,
    STOP_WORDS,
    FakeImageEncoder,
    FakeVad,
    decode_label,
    label_axis,
    write_label_image,
)
from fixtures.generator import Synth, SynthSpec, label_code
from helpers import (
    ACCOUNT,
    CREATOR,
    CREATOR_ACCOUNT,
    SERIES,
    cleaned_episode,
    ctx_events,
    make_episode,
    snapshot,
    synthetic_phash,
    with_config,
)
from rushes.analyze.analyze import AnalyzeStep
from rushes.analyze.facets import facets_done, facets_needed
from rushes.analyze.index import blob_vector, search_scenes
from rushes.analyze.tags import TAG_PROMPTS, character_presence, zero_shot_tags
from rushes.analyze.vision import VisionStep
from rushes.config import load_config
from rushes.context import Context, Filters
from rushes.db.models import Scene
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.media.frames import extract_frame
from rushes.models.hub import ModelHub
from rushes.runner import StepReport, run_step
from rushes.schemas import AudioFile, ScenesFile, Shot, write_json

SMALL = (320, 180)
SHOT_MS = 2000
MODEL = "ViT-B-32/datacomp_xl_s13b_b90k"
EditYaml = Callable[[Path, Callable[[Any], Any]], None]


# --- helpers ----------------------------------------------------------------------------------


def _folder(ctx: Context, episode_id: int) -> Path:
    return ctx.paths.episode_dir(episode_key(episode_id))


def _episode(
    ctx: Context,
    labels: Sequence[str | None],
    *,
    owner: str = ACCOUNT,
    creator_id: str | None = None,
    reverse_rows: bool = False,
) -> int:
    """An analyzed episode with one shot per label: scenes rows, scenes.json, one keyframe each.

    A keyframe carries the generator's pattern of its label (None: a plain picture). With
    `reverse_rows` the `scenes` rows are inserted last shot first, so that SQL ids and shot ids
    differ (C40).
    """
    shots = [
        Shot(
            id=i,
            start_ms=i * SHOT_MS,
            end_ms=(i + 1) * SHOT_MS,
            keyframe_ms=i * SHOT_MS + SHOT_MS // 2,
            motion=0.5,
            loudness_lufs=None,
            speech_ratio=0.0,
            phash=synthetic_phash(i),
        )
        for i in range(len(labels))
    ]
    with Session(ctx.engine) as session:
        episode = make_episode(
            session, owner=owner, series_id=None if creator_id else SERIES, creator_id=creator_id
        )
        episode.duration_ms = SHOT_MS * len(labels)
        for shot in reversed(shots) if reverse_rows else shots:
            session.add(Scene(episode_id=episode.id, start_ms=shot.start_ms, end_ms=shot.end_ms))
        session.commit()
        episode_id = episode.id
    folder = _folder(ctx, episode_id)
    write_json(
        ScenesFile(
            version=1,
            episode_id=episode_id,
            duration_ms=SHOT_MS * len(labels),
            detector={},
            shots=shots,
        ),
        folder / "scenes.json",
    )
    for shot, label in zip(shots, labels, strict=True):
        image = folder / "keyframes" / f"{shot.id}.jpg"
        if label is None:
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", SMALL, (60, 90, 140)).save(image)
        else:
            write_label_image(label, image, SMALL)
    return episode_id


def _vision(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, VisionStep(), Filters(**filters))


def _with_encoder(ctx: Context, encoder: FakeImageEncoder) -> Context:
    """The same context whose CLIP is `encoder` (to read what it was asked to encode)."""
    hub = ModelHub(
        ctx.paths, ctx.config.settings, factories={"vad": FakeVad, "clip": lambda: encoder}
    )
    return dataclasses.replace(ctx, models=hub)


def _with_characters(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml, names: Sequence[str]
) -> Context:
    """The series tom_and_jerry gets characters, each with one reference image of its name."""
    for name in names:
        write_label_image(name, config_dir / "refs" / f"{name}.png", SMALL)
    edit_yaml(
        config_dir / "series" / f"{SERIES}.yaml",
        lambda d: d.update(
            characters=[{"id": name, "refs": [f"refs/{name}.png"]} for name in names]
        ),
    )
    return with_config(ctx, config_dir)


def _rows(ctx: Context, episode_id: int) -> list[dict[str, Any]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.start_ms, s.tags_json, s.characters_json, v.model, v.dim, v.embedding "
                "FROM scenes s LEFT JOIN scene_vectors v ON v.scene_id = s.id "
                "WHERE s.episode_id = :e ORDER BY s.start_ms"
            ),
            {"e": episode_id},
        ).mappings()
        return [dict(row) for row in rows]


def _tags(rows: Sequence[dict[str, Any]]) -> list[list[str]]:
    return [json.loads(row["tags_json"]) for row in rows]


def _characters(rows: Sequence[dict[str, Any]]) -> list[list[str]]:
    return [json.loads(row["characters_json"]) for row in rows]


def _unit(*values: float) -> NDArray[np.float32]:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


# --- the generator's labels -------------------------------------------------------------------

VOCABULARY = [
    *("BANANA", "ROCKET", "GUITAR", "PIZZA", "ANCHOR", "CASTLE", "DRAGON", "FLOWER", "ROBOT"),
    *("TOM", "JERRY", "chasing", "falling", "explosion", "trap", "hit", "food", "sleep"),
    *("scared", "angry", "music", "a", "b", "c", "d", "pizza", "rocket"),
]


def test_label_codes_are_distinct_and_ignore_case() -> None:
    words = {word.upper() for word in VOCABULARY}
    codes = [label_code(word) for word in words]
    assert len(set(codes)) == len(codes)
    assert label_code("banana") == label_code("BANANA")
    assert all(0 <= code < 1 << 16 for code in codes)


def test_fake_image_encoder_axes_do_not_collide_for_the_test_vocabulary() -> None:
    """Labels used by the tests and the words of the tag prompts, each on its own axis."""
    prompt_words = {w for p in TAG_PROMPTS.values() for w in p.split() if w not in STOP_WORDS}
    words = {word.lower() for word in [*VOCABULARY, *prompt_words]}
    axes = {label_axis(word) for word in words}
    assert len(axes) == len(words), "two words share an axis: change a test word"
    assert NO_LABEL not in axes and FakeImageEncoder.dim == LABEL_AXES + 1


@pytest.mark.parametrize("size", [(1280, 720), SMALL, (160, 90)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_shot_labels_draw_a_distinct_stable_pattern_per_word(
    synth: Synth, tmp_path: Path, size: tuple[int, int]
) -> None:
    spec = SynthSpec(
        duration_ms=6000, size=size, fps=10, shots_ms=[2000, 4000], shot_labels=["BANANA", "rocket"]
    )
    video = synth(spec)
    codes = []
    for index, at in enumerate((1000, 3000, 5000)):  # the third shot has no label
        image = tmp_path / f"{index}.jpg"
        extract_frame(video, at, image)
        codes.append(decode_label(image))
    assert codes == [label_code("BANANA"), label_code("ROCKET"), None]
    assert codes[0] != codes[1]


def test_shot_labels_keep_the_pattern_for_the_whole_shot(synth: Synth, tmp_path: Path) -> None:
    video = synth(
        SynthSpec(
            duration_ms=4000, size=SMALL, fps=10, shots_ms=[2000], shot_labels=["BANANA", "ROCKET"]
        )
    )
    codes = []
    for index, at in enumerate((100, 1000, 1900, 2100, 3000, 3900)):
        image = tmp_path / f"{index}.jpg"
        extract_frame(video, at, image)
        codes.append(decode_label(image))
    assert codes == [label_code("BANANA")] * 3 + [label_code("ROCKET")] * 3


def test_shot_labels_refuse_more_labels_than_shots(synth: Synth) -> None:
    with pytest.raises(ValueError, match="étiquettes"):
        synth(SynthSpec(duration_ms=2000, size=SMALL, fps=10, shot_labels=["A", "B"]))


def test_fake_image_encoder_maps_words_and_prompts_consistently(tmp_path: Path) -> None:
    encoder = FakeImageEncoder()
    images = [write_label_image(w, tmp_path / f"{w}.png") for w in ("banana", "chasing")]
    plain = tmp_path / "plain.png"
    Image.new("RGB", SMALL, (60, 90, 140)).save(plain)
    vectors = encoder.encode_images([*images, plain])
    texts = encoder.encode_texts(["a banana", TAG_PROMPTS["chase"], TAG_PROMPTS["fall"]])
    cos = vectors @ texts.T
    assert cos[0].tolist() == pytest.approx([1.0, 0.0, 0.0])
    assert cos[1].tolist() == pytest.approx([0.0, 3**-0.5, 0.0])  # "chasing" among 3 words
    assert cos[2].tolist() == [0.0, 0.0, 0.0]  # no label: matches nothing
    assert vectors.shape == (3, encoder.dim) and np.allclose(np.linalg.norm(vectors, axis=1), 1)


# --- tags and characters ----------------------------------------------------------------------


def test_zero_shot_tags_top3_above_threshold() -> None:
    labels = ["a", "b", "c", "d", "e"]
    txt = np.eye(5, dtype=np.float32)  # image @ txt.T is the image itself
    img = np.array(
        [
            [0.80, 0.50, 0.30, 0.25, 0.05],  # four above 0.24: only the best three
            [0.20, 0.10, 0.20, 0.05, 0.10],  # none above 0.24
            [0.10, 0.30, 0.10, 0.10, 0.10],  # one
            [0.30, 0.30, 0.30, 0.30, 0.30],  # equal: label order decides
        ],
        dtype=np.float32,
    )
    found = zero_shot_tags(img, txt, labels, min_cos=0.24, top=3)
    assert found == [["a", "b", "c"], [], ["b"], ["a", "b", "c"]]
    assert zero_shot_tags(img, txt, labels, min_cos=0.24, top=1)[0] == ["a"]
    assert zero_shot_tags(img[:0], txt, labels, min_cos=0.24, top=3) == []


def test_zero_shot_tags_threshold_is_inclusive() -> None:
    txt = np.eye(2, dtype=np.float32)
    img = np.array([[0.5, 0.25]], dtype=np.float32)
    assert zero_shot_tags(img, txt, ["a", "b"], min_cos=0.25, top=3) == [["a", "b"]]


def test_character_presence_from_refs() -> None:
    refs = {"tom": _unit(1, 0), "jerry": _unit(0.8, 0.6)}
    img = np.stack([_unit(1, 0), _unit(0, 1), _unit(0.95, 0.31), _unit(1, 1)])
    found = character_presence(img, refs, min_cos=0.82)
    # tom 1.0 / jerry 0.8 ; neither (jerry 0.6) ; both ; tom and jerry 0.7 and 0.99
    assert found == [["tom"], [], ["jerry", "tom"], ["jerry"]]
    assert character_presence(img, {}, min_cos=0.82) == [[], [], [], []]


def test_character_presence_threshold_is_inclusive() -> None:
    refs = {"tom": np.array([1.0, 0.0], dtype=np.float32)}
    img = np.array([[0.5, 0.5], [0.25, 0.5]], dtype=np.float32)  # cosines 0.5 and 0.25, exact
    assert character_presence(img, refs, min_cos=0.5) == [["tom"], []]


class VectorEncoder:
    """Images are the vectors a test gives by file name (`a.png`, `0.jpg`...); prompts are axes."""

    dim = 3

    def __init__(self, vectors: dict[str, Sequence[float]]) -> None:
        self.vectors = {name: _unit(*v) for name, v in vectors.items()}

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
        return np.stack([self.vectors[path.name] for path in images])

    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.eye(len(texts), self.dim, dtype=np.float32)


def test_vision_character_vector_is_the_mean_of_its_references(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml
) -> None:
    # two references 40 degrees either side of the shot: each alone is at cos 0.766 (< 0.82), their
    # mean points at the shot (cos 1)
    cos, sin = math.cos(math.radians(40)), math.sin(math.radians(40))
    a, b = (cos, sin), (cos, -sin)
    shot = (1.0, 0.0, 0.0)
    for name in ("a.png", "b.png"):
        (config_dir / "refs").mkdir(exist_ok=True)
        write_label_image("x", config_dir / "refs" / name, SMALL)
    edit_yaml(
        config_dir / "series" / f"{SERIES}.yaml",
        lambda d: d.update(characters=[{"id": "tom", "refs": ["refs/a.png", "refs/b.png"]}]),
    )
    encoder = VectorEncoder({"a.png": (*a, 0), "b.png": (*b, 0), "0.jpg": shot, "1.jpg": (0, 1, 0)})
    ctx = dataclasses.replace(
        with_config(ctx, config_dir),
        models=ModelHub(ctx.paths, ctx.config.settings, factories={"clip": lambda: encoder}),
    )
    assert float(encoder.vectors["a.png"] @ encoder.vectors["0.jpg"]) < 0.82
    assert float(encoder.vectors["b.png"] @ encoder.vectors["0.jpg"]) < 0.82
    episode_id = _episode(ctx, ["x", "y"])
    assert _vision(ctx).done == 1
    assert _characters(_rows(ctx, episode_id)) == [["tom"], []]


# --- settings ---------------------------------------------------------------------------------


def test_analyze_settings_start_values_and_ranges(ctx: Context) -> None:
    from pydantic import ValidationError

    from rushes.config import AnalyzeSettings

    assert ctx.config.settings.analyze == AnalyzeSettings(
        tag_min_cos=0.24, tag_top=3, character_min_cos=0.82, clip_batch=32
    )
    for bad in ({"clip_batch": 0}, {"tag_top": 0}, {"tag_min_cos": 1.5}, {"character_min_cos": -2}):
        with pytest.raises(ValidationError):
            AnalyzeSettings(**bad)
    with pytest.raises(ValidationError):
        AnalyzeSettings(unknown=1)  # type: ignore[call-arg]


def test_clip_pretrained_is_the_one_of_the_manifest_weights(
    config_dir: Path, edit_yaml: EditYaml
) -> None:
    from rushes.config import ConfigError

    assert load_config(config_dir).settings.models.clip_pretrained == "datacomp_xl_s13b_b90k"
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(models={"clip_pretrained": "other"}))
    with pytest.raises(ConfigError) as caught:
        load_config(config_dir)
    assert "clip_pretrained" in str(caught.value.issues[0])


# --- facets -----------------------------------------------------------------------------------


def test_facets_needed_base_vision_sounds_always_words_for_dialogue_or_creators(
    ctx: Context, session: Session, config_dir: Path, edit_yaml: EditYaml
) -> None:
    cartoon = make_episode(session)
    creator = make_episode(session, owner=CREATOR_ACCOUNT, creator_id=CREATOR)
    session.commit()
    assert facets_needed(ctx.config, session, cartoon.id) == {"base", "vision", "sounds"}
    assert facets_needed(ctx.config, session, creator.id) == {"base", "vision", "sounds", "words"}

    edit_yaml(config_dir / "series" / f"{SERIES}.yaml", lambda d: d.update(audio="dialogue_en"))
    talking = load_config(config_dir)
    assert facets_needed(talking, session, cartoon.id) == {"base", "vision", "sounds", "words"}


def _done(ctx: Context, episode_id: int) -> set[str]:
    with Session(ctx.engine) as session:  # a fresh read each time: no stale snapshot
        return set(facets_done(session, ctx.paths, episode_id))


def test_facets_done_reads_each_facet_from_its_source(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana"])
    folder = _folder(ctx, episode_id)
    assert _done(ctx, episode_id) == {"base"}

    assert _vision(ctx).done == 1
    assert _done(ctx, episode_id) == {"base", "vision"}

    audio = AudioFile(
        version=1, episode_id=episode_id, integrated_lufs=-20.0, loudness=[], speech=[]
    )
    write_json(audio, folder / "audio.json")  # events None: sounds not analyzed yet
    assert _done(ctx, episode_id) == {"base", "vision"}
    write_json(audio.model_copy(update={"events": []}), folder / "audio.json")  # analyzed, no event
    assert _done(ctx, episode_id) == {"base", "vision", "sounds"}

    (folder / "words.json").write_text("{}", encoding="utf-8")
    assert _done(ctx, episode_id) == {"base", "vision", "sounds", "words"}


def test_facets_done_vision_needs_a_vector_for_every_scene(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana"])
    assert _vision(ctx).done == 1
    with ctx.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM scene_vectors WHERE scene_id = (SELECT min(id) FROM scenes)")
        )
    assert _done(ctx, episode_id) == {"base"}


def test_facets_done_base_is_the_analyzed_status(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing"])
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='cleaned' WHERE id=:e"), {"e": episode_id})
    assert "base" not in _done(ctx, episode_id)


def test_facets_done_vision_needs_the_episode_to_be_analyzed(ctx: Context) -> None:
    with Session(ctx.engine) as session:  # cleaned, not analyzed: no scene yet
        episode = make_episode(session)
        episode.status = "cleaned"
        session.commit()
        episode_id = episode.id
    assert _done(ctx, episode_id) == set()
    done = _episode(ctx, ["chasing"])  # the same, once analyzed and seen
    assert _vision(ctx).done == 1
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='cleaned' WHERE id=:e"), {"e": done})
    assert _done(ctx, done) == set()  # vectors of a cleaned episode do not make it "seen"


# --- the step ---------------------------------------------------------------------------------


def test_vision_step_writes_vectors_tags_and_characters(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml
) -> None:
    ctx = _with_characters(ctx, config_dir, edit_yaml, ["TOM", "JERRY"])
    episode_id = _episode(ctx, ["chasing", "TOM", "explosion", "banana", None])
    report = _vision(ctx)
    assert (report.done, report.failed) == (1, 0), ctx_events(ctx)

    rows = _rows(ctx, episode_id)
    assert _tags(rows) == [["chase"], [], ["explosion"], [], []]
    assert _characters(rows) == [[], ["TOM"], [], [], []]
    assert {row["model"] for row in rows} == {MODEL}
    assert {row["dim"] for row in rows} == {FakeImageEncoder.dim}
    expected = FakeImageEncoder().encode_images(
        [_folder(ctx, episode_id) / "keyframes" / f"{i}.jpg" for i in range(5)]
    )
    for row, vector in zip(rows, expected, strict=True):
        assert np.array_equal(blob_vector(row["embedding"], row["dim"]), vector)
    assert _done(ctx, episode_id) == {"base", "vision"}


class PromptAxesEncoder:
    """One axis per tag prompt; every image has the same few cosines (tags not in score order)."""

    dim = len(TAG_PROMPTS)
    scores: ClassVar[dict[str, float]] = {
        "music": 0.5,
        "chase": 0.4,
        "angry": 0.3,
        "hit": 0.25,
        "food": 0.1,
    }

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
        row = [self.scores.get(label, 0.0) for label in TAG_PROMPTS]
        return np.array([row] * len(images), dtype=np.float32)

    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.eye(len(texts), dtype=np.float32)


def test_vision_stores_the_best_three_tags_as_a_sorted_json_list(ctx: Context) -> None:
    ctx = dataclasses.replace(
        ctx,
        models=ModelHub(ctx.paths, ctx.config.settings, factories={"clip": PromptAxesEncoder}),
    )
    episode_id = _episode(ctx, ["chasing"])
    assert _vision(ctx).done == 1
    with ctx.engine.connect() as conn:
        raw = conn.execute(
            text("SELECT tags_json, characters_json FROM scenes WHERE episode_id=:e"),
            {"e": episode_id},
        ).one()
    # music 0.5, chase 0.4, angry 0.3 beat hit 0.25 (also above 0.24): stored in name order
    assert tuple(raw) == ('["angry", "chase", "music"]', "[]")  # no reference image: no character


def test_vision_matches_scenes_to_shots_by_start_ms_not_by_id(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana", "explosion"], reverse_rows=True)
    assert _vision(ctx).done == 1
    rows = _rows(ctx, episode_id)  # ordered by start_ms, whatever the SQL ids
    assert [row["start_ms"] for row in rows] == [0, SHOT_MS, 2 * SHOT_MS]
    assert _tags(rows) == [["chase"], [], ["explosion"]]


def test_vision_encodes_keyframes_in_batches_of_clip_batch(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml
) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(analyze={"clip_batch": 3}))
    encoder = FakeImageEncoder()
    ctx = _with_encoder(with_config(ctx, config_dir), encoder)
    _episode(ctx, ["a", "b", "c", "d"])
    assert _vision(ctx).done == 1
    assert [len(call) for call in encoder.image_calls] == [3, 1]


def test_vision_encodes_prompts_and_references_once_per_step(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml
) -> None:
    encoder = FakeImageEncoder()
    ctx = _with_encoder(_with_characters(ctx, config_dir, edit_yaml, ["TOM", "JERRY"]), encoder)
    _episode(ctx, ["TOM", "banana"])
    _episode(ctx, ["JERRY", "TOM"])
    assert _vision(ctx).done == 2
    assert encoder.text_calls == [list(TAG_PROMPTS.values())]
    encoded = [path for call in encoder.image_calls for path in call]
    refs = [path for path in encoded if path.parent.resolve() == (config_dir / "refs").resolve()]
    assert sorted(path.name for path in refs) == ["JERRY.png", "TOM.png"]  # each read once


def test_r19_clip_loaded_once_for_many_episodes(ctx: Context) -> None:
    for labels in (["chasing", "banana"], ["falling"], ["TOM", "rocket", "pizza"]):
        _episode(ctx, labels)
    assert _vision(ctx).done == 3
    assert ctx.models.loads()["clip"] == 1


def test_r16_vision_rerun_changes_nothing(
    ctx: Context, config_dir: Path, edit_yaml: EditYaml
) -> None:
    ctx = _with_characters(ctx, config_dir, edit_yaml, ["TOM"])
    _episode(ctx, ["chasing", "TOM", "banana"])
    assert _vision(ctx).done == 1
    before = snapshot(ctx)
    again = _vision(ctx)
    assert (again.done, again.skipped, again.failed) == (0, 0, 0)  # nothing is selected
    assert snapshot(ctx) == before
    forced = _vision(ctx, force=True)  # recomputed: the very same bytes
    assert (forced.done, forced.failed) == (1, 0)
    assert snapshot(ctx) == before


def test_vision_selects_analyzed_episodes_lacking_the_vision_facet(ctx: Context) -> None:
    step = VisionStep()
    done = _episode(ctx, ["chasing"])
    todo = _episode(ctx, ["banana"])
    assert _vision(ctx, episode=done).done == 1
    assert step.select(ctx, Filters()) == [todo]
    assert step.select(ctx, Filters(force=True)) == [done, todo]
    assert step.select(ctx, Filters(episode=done)) == []
    assert step.select(ctx, Filters(series="oggy")) == []
    assert step.select(ctx, Filters(account="nobody")) == []
    assert step.select(ctx, Filters(series=SERIES, account=ACCOUNT)) == [todo]
    assert step.key(todo) == episode_key(todo)
    assert VisionStep.name == "vision" and VisionStep.heavy is True


def test_vision_redoes_the_vectors_of_another_model(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana"])
    assert _vision(ctx).done == 1
    with ctx.engine.begin() as conn:  # as if the weights had changed since
        conn.execute(text("UPDATE scene_vectors SET model='ViT-B-32/old'"))
    assert VisionStep().select(ctx, Filters()) == [episode_id]
    assert _done(ctx, episode_id) == {"base"}
    assert _vision(ctx).done == 1
    rows = _rows(ctx, episode_id)
    assert {row["model"] for row in rows} == {MODEL}  # every row replaced, none left behind
    assert len(rows) == 2 and _tags(rows) == [["chase"], []]
    assert VisionStep().select(ctx, Filters()) == []


def test_vision_refuses_an_encoder_of_another_model(ctx: Context) -> None:
    """Selection looks for the manifest's model: an encoder of another one would loop forever."""

    class Other(FakeImageEncoder):
        model_name = "ViT-L-14/other"

    ctx = _with_encoder(ctx, Other())
    episode_id = _episode(ctx, ["chasing"])
    with pytest.raises(RushesError, match="modèle"):
        VisionStep().run(ctx, episode_id, force=False)
    assert {row["embedding"] for row in _rows(ctx, episode_id)} == {None}
    assert _vision(ctx).failed == 1


def test_vision_releases_the_models_when_the_step_ends(ctx: Context) -> None:
    _episode(ctx, ["chasing"])
    assert _vision(ctx).done == 1
    assert ctx.models.loads()["clip"] == 1
    ctx.models.clip()  # reloaded: the step let go of it (the run goes on with FFmpeg steps)
    assert ctx.models.loads()["clip"] == 2


def test_vision_writes_nothing_when_a_scene_changed_under_it(ctx: Context) -> None:
    """The guard on the updated rows: a scene that left its episode meanwhile cancels the lot."""
    episode_id = _episode(ctx, ["chasing", "banana"])
    other = _episode(ctx, ["falling"])

    class Moving(FakeImageEncoder):
        def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
            with ctx.engine.begin() as conn:  # a scene moves to another episode while encoding
                conn.execute(
                    text("UPDATE scenes SET episode_id=:o WHERE episode_id=:e AND start_ms=0"),
                    {"o": other, "e": episode_id},
                )
            return super().encode_images(images)

    ctx = dataclasses.replace(
        ctx, models=ModelHub(ctx.paths, ctx.config.settings, factories={"clip": Moving})
    )
    with pytest.raises(RushesError, match="scènes ont changé"):
        VisionStep().run(ctx, episode_id, force=False)
    with ctx.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM scene_vectors")).scalar_one() == 0
        tags = conn.execute(text("SELECT tags_json FROM scenes WHERE tags_json IS NOT NULL")).all()
    assert tags == []


def test_vision_ignores_episodes_that_are_not_analyzed_or_have_no_scene(ctx: Context) -> None:
    cleaned = _episode(ctx, ["chasing"])
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE episodes SET status='cleaned' WHERE id=:e"), {"e": cleaned})
    with Session(ctx.engine) as session:  # analyzed, but nothing to look at
        make_episode(session)
        session.commit()
    assert VisionStep().select(ctx, Filters()) == []
    assert VisionStep().select(ctx, Filters(force=True)) == []


def test_vision_covers_creator_and_community_episodes_too(ctx: Context) -> None:
    creator = _episode(ctx, ["falling"], owner=CREATOR_ACCOUNT, creator_id=CREATOR)
    community = _episode(ctx, ["chasing"])
    with ctx.engine.begin() as conn:
        conn.execute(
            text("UPDATE episodes SET provenance='community' WHERE id=:e"), {"e": community}
        )
    assert _vision(ctx).done == 2
    assert _tags(_rows(ctx, creator)) == [["fall"]]
    assert _tags(_rows(ctx, community)) == [["chase"]]


def _clean_video(
    ctx: Context,
    episode_id: int,
    synth: Synth,
    labels: Sequence[str],
    *,
    changes_ms: Sequence[int] | None = None,
) -> Path:
    """Install as clean.mp4 a video whose shots carry `labels`.

    The shots change every SHOT_MS (as in `_episode`) unless `changes_ms` moves the changes.
    """
    video = synth(
        SynthSpec(
            duration_ms=SHOT_MS * len(labels),
            size=SMALL,
            fps=10,
            shots_ms=list(changes_ms or [SHOT_MS * i for i in range(1, len(labels))]),
            shot_labels=labels,
        )
    )
    target = _folder(ctx, episode_id) / "clean.mp4"
    shutil.copy2(video, target)
    return target


def test_vision_recreates_a_missing_keyframe_from_clean_mp4(ctx: Context, synth: Synth) -> None:
    labels = ["chasing", "banana", "explosion"]
    episode_id = _episode(ctx, labels)
    # the video changes shot 500 ms after the shots of scenes.json start: at a shot's start_ms
    # (2000, 4000) it still shows the previous label, only keyframe_ms (3000, 5000) shows its own
    _clean_video(ctx, episode_id, synth, labels, changes_ms=[2500, 4500])
    keyframes = _folder(ctx, episode_id) / "keyframes"
    (keyframes / "1.jpg").unlink()
    (keyframes / "2.jpg").unlink()
    report = _vision(ctx)
    assert (report.done, report.failed) == (1, 0), ctx_events(ctx)
    assert decode_label(keyframes / "1.jpg") == label_code("banana")  # read at keyframe_ms
    assert decode_label(keyframes / "2.jpg") == label_code("explosion")
    assert _tags(_rows(ctx, episode_id)) == [["chase"], [], ["explosion"]]
    assert sorted(p.name for p in keyframes.iterdir()) == ["0.jpg", "1.jpg", "2.jpg"]


def test_vision_missing_keyframe_without_clean_mp4_fails_and_writes_nothing(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana", "explosion"])
    (_folder(ctx, episode_id) / "keyframes" / "1.jpg").unlink()
    with pytest.raises(MediaError, match=r"clean\.mp4") as caught:
        VisionStep().run(ctx, episode_id, force=False)
    # a hint that can work (`clean` and `analyze` refuse an analyzed episode): the folder to restore
    assert "analyze --force" not in (caught.value.hint or "")
    assert str(_folder(ctx, episode_id)) in (caught.value.hint or "")
    report = _vision(ctx)
    assert (report.done, report.failed) == (0, 1)
    rows = _rows(ctx, episode_id)
    assert {row["embedding"] for row in rows} == {None}
    assert {row["tags_json"] for row in rows} == {None}


def test_vision_refuses_scenes_that_do_not_match_scenes_json(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana"])
    with ctx.engine.begin() as conn:
        conn.execute(
            text("UPDATE scenes SET start_ms = start_ms + 1 WHERE episode_id=:e"), {"e": episode_id}
        )
    report = _vision(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert {row["embedding"] for row in _rows(ctx, episode_id)} == {None}


def test_vision_without_scenes_json_fails_with_a_hint(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing"])
    (_folder(ctx, episode_id) / "scenes.json").unlink()
    assert _vision(ctx).failed == 1
    failed = [e for e in ctx_events(ctx) if e.get("status") == "failed"]
    assert "scenes.json" in failed[0]["message"]


def test_analyze_force_drops_the_vectors_with_their_scenes(ctx: Context) -> None:
    episode_id = _episode(ctx, ["chasing", "banana"])
    assert _vision(ctx).done == 1
    with ctx.engine.begin() as conn:  # what analyze does when it recomputes an episode
        conn.execute(text("DELETE FROM scenes WHERE episode_id=:e"), {"e": episode_id})
        assert conn.execute(text("SELECT count(*) FROM scene_vectors")).scalar_one() == 0


# --- on a synthetic video, end to end ---------------------------------------------------------


def test_vision_on_a_synthetic_episode_reads_the_labels_the_generator_drew(
    ctx: Context, synth: Synth
) -> None:
    labels = ["chasing", "banana", "explosion"]
    video = synth(
        SynthSpec(
            duration_ms=SHOT_MS * 3,
            size=SMALL,
            fps=10,
            shots_ms=[SHOT_MS, 2 * SHOT_MS],
            shot_labels=labels,
        )
    )
    episode_id = cleaned_episode(ctx, video)
    report = run_step(ctx, AnalyzeStep(), Filters())
    assert (report.done, report.failed) == (1, 0), ctx_events(ctx)
    assert _vision(ctx).done == 1
    rows = _rows(ctx, episode_id)
    assert _tags(rows) == [["chase"], [], ["explosion"]]
    by_start = sorted(_scene_ids(ctx, episode_id), key=lambda row: row["start_ms"])
    with Session(ctx.engine) as session:  # "a banana" finds the second shot first
        best = search_scenes(session, ctx.models.clip(), "a banana", k=1)
    assert best[0][0] == by_start[1]["scene_id"]


def _scene_ids(ctx: Context, episode_id: int) -> list[dict[str, Any]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id AS scene_id, start_ms FROM scenes WHERE episode_id=:e ORDER BY id"),
            {"e": episode_id},
        ).mappings()
        return [dict(row) for row in rows]


# --- real CLIP (slow, opt-in) ------------------------------------------------------------------

WORDS = [
    "BANANA", "ROCKET", "GUITAR", "PIZZA", "ANCHOR", "CASTLE", "DRAGON", "FLOWER", "ROBOT", "CAT"
]  # fmt: skip
ENV_WEIGHTS = "RUSHES_TEST_CLIP_WEIGHTS"


def _write_word(word: str, out: Path) -> None:
    """A shot's key image: the word in big letters (CLIP reads text)."""
    picture = Image.new("RGB", (512, 288), (245, 245, 245))
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=96)
    box = draw.textbbox((0, 0), word, font=font)
    position = ((512 - box[2] + box[0]) // 2 - box[0], (288 - box[3] + box[1]) // 2 - box[1])
    draw.text(position, word, fill=(20, 20, 20), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)


@pytest.mark.slow
def test_free_text_search_finds_known_gag_top5(
    ctx: Context, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real CLIP: 10 shots, each a big word; "a banana" must bring the BANANA shot into the top 5.

    Opt-in: set RUSHES_TEST_CLIP_WEIGHTS to the `open_clip_model.safetensors` file of the manifest
    (the tests never read data/ and never download); without it the test is skipped. Run it with
    `-s` to see the measured cosines (to judge the thresholds 0.24 and 0.82 on real vectors).
    The shots are key images rendered with PIL (a drawbox pattern means nothing to a real model).
    """
    given = os.environ.get(ENV_WEIGHTS, "")
    weights = Path(given) if given else None
    if weights is None or not weights.is_file():
        pytest.skip(f"{ENV_WEIGHTS} non défini ou fichier absent : test CLIP réel ignoré")
    from rushes.models.fetch import load_manifest

    entry = next(e for e in load_manifest() if e.name == "clip")
    target = ctx.paths.cache_models / entry.file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(weights.resolve())  # the hub's own factory finds them in this test's folder
    ctx = dataclasses.replace(
        ctx, models=ModelHub(ctx.paths, ctx.config.settings, factories={"vad": FakeVad})
    )
    encoder = ctx.models.clip()  # the real factory, loaded once; the step reuses it (R19)
    episode_id = _episode(ctx, [None] * len(WORDS))
    for index, word in enumerate(WORDS):
        _write_word(word, _folder(ctx, episode_id) / "keyframes" / f"{index}.jpg")

    report = _vision(ctx)
    assert (report.done, report.failed) == (1, 0), ctx_events(ctx)
    assert ctx.models.loads() == {"clip": 1}
    assert {row["model"] for row in _rows(ctx, episode_id)} == {MODEL}
    scenes = {row["start_ms"] // SHOT_MS: row["scene_id"] for row in _scene_ids(ctx, episode_id)}
    with Session(ctx.engine) as session:
        ranked = search_scenes(session, encoder, "a banana", k=len(WORDS))
    by_scene = {scene: word for word, scene in ((w, scenes[i]) for i, w in enumerate(WORDS))}
    with capsys.disabled():
        print("\ncosinus de « a banana » :", [(by_scene[s], round(c, 3)) for s, c in ranked])
        for row in _rows(ctx, episode_id):
            print("étiquettes", row["tags_json"], row["start_ms"] // SHOT_MS)
    assert scenes[WORDS.index("BANANA")] in [scene for scene, _ in ranked[:5]]
