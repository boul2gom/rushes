"""Scene vector index (task 12a): blobs and free-text search over `scene_vectors`."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from helpers import ACCOUNT, ACCOUNT_OTHER_BRAND, make_episode
from rushes.analyze.index import blob_vector, search_scenes, vector_blob
from rushes.db.models import Episode, Scene, SceneVector


class StubEncoder:
    """Encodes every text to the vector it was given; never sees an image."""

    dim = 3
    model_name = "stub"

    def __init__(self, query: Sequence[float]) -> None:
        self.query = np.asarray(query, dtype=np.float32)

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
        raise AssertionError("search never encodes images")

    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.stack([self.query] * len(texts))


def _unit(*values: float) -> NDArray[np.float32]:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _scene(
    session: Session,
    episode: Episode,
    vector: NDArray[np.float32],
    dim: int = 3,
    model: str = "stub",
) -> int:
    scene = Scene(episode_id=episode.id, start_ms=0, end_ms=1000)
    session.add(scene)
    session.flush()
    session.add(SceneVector(scene_id=scene.id, model=model, dim=dim, embedding=vector_blob(vector)))
    session.flush()
    return scene.id


def test_vector_blob_roundtrip() -> None:
    vector = _unit(1, 2, 3)
    blob = vector_blob(vector)
    assert len(blob) == 3 * 4
    assert np.array_equal(blob_vector(blob, 3), vector)
    assert blob_vector(blob, 3).dtype == np.float32


def test_vector_blob_is_little_endian_float32() -> None:
    assert vector_blob(np.array([1.0, -2.0], dtype=np.float32)).hex() == "0000803f000000c0"
    # whatever the input layout or type, the stored form is the same
    wide = np.array([[1.0, 9.0], [-2.0, 9.0]], dtype=np.float64)[:, 0]
    assert vector_blob(wide).hex() == "0000803f000000c0"


def test_blob_vector_refuses_a_blob_of_another_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        blob_vector(vector_blob(_unit(1, 2, 3)), 4)


def test_search_returns_top_k_filtered_by_account(session: Session) -> None:
    ours = make_episode(session, owner=ACCOUNT, series_id="tom_and_jerry")
    theirs = make_episode(session, owner=ACCOUNT_OTHER_BRAND, series_id="oggy")
    best = _scene(session, ours, _unit(1, 0, 0))
    near = _scene(session, ours, _unit(3, 4, 0))  # cosine 0.6
    far = _scene(session, ours, _unit(0, 1, 0))
    rival = _scene(session, theirs, _unit(1, 0.1, 0))
    other_model = _scene(session, ours, _unit(1, 0), dim=2)  # another dimension: not comparable
    session.commit()
    encoder = StubEncoder([1, 0, 0])

    everyone = search_scenes(session, encoder, "anything", k=10)
    assert [scene for scene, _ in everyone] == [best, rival, near, far]  # other_model left out
    assert other_model not in [scene for scene, _ in everyone]
    assert everyone[0][1] == pytest.approx(1.0)
    assert everyone[2][1] == pytest.approx(0.6)

    mine = search_scenes(session, encoder, "anything", account_id=ACCOUNT, k=2)
    assert [scene for scene, _ in mine] == [best, near]  # top 2 of the account's scenes

    assert [s for s, _ in search_scenes(session, encoder, "x", account_id=ACCOUNT_OTHER_BRAND)] == [
        rival
    ]
    assert search_scenes(session, encoder, "x", account_id="nobody") == []


def test_search_filters_by_series_and_combines_with_the_account(session: Session) -> None:
    tom = make_episode(session, owner=ACCOUNT, series_id="tom_and_jerry")
    oggy = make_episode(session, owner=ACCOUNT, series_id="oggy")
    in_tom = _scene(session, tom, _unit(1, 0, 0))
    in_oggy = _scene(session, oggy, _unit(1, 1, 0))
    session.commit()
    encoder = StubEncoder([1, 0, 0])
    assert [s for s, _ in search_scenes(session, encoder, "x", series_id="oggy")] == [in_oggy]
    assert [s for s, _ in search_scenes(session, encoder, "x", series_id="tom_and_jerry")] == [
        in_tom
    ]
    both = search_scenes(session, encoder, "x", series_id="oggy", account_id=ACCOUNT_OTHER_BRAND)
    assert both == []


def test_search_ties_go_to_the_lowest_scene_id(session: Session) -> None:
    episode = make_episode(session)
    ids = [_scene(session, episode, _unit(1, 0, 0)) for _ in range(4)]
    session.commit()
    found = search_scenes(session, StubEncoder([1, 0, 0]), "x", k=3)
    assert [scene for scene, _ in found] == ids[:3]


def test_search_on_an_empty_index_is_empty(session: Session) -> None:
    assert search_scenes(session, StubEncoder([1, 0, 0]), "x") == []


def test_search_ignores_vectors_of_another_model_of_the_same_dimension(session: Session) -> None:
    episode = make_episode(session)
    ours = _scene(session, episode, _unit(1, 0, 0))
    _scene(session, episode, _unit(1, 0, 0), model="ViT-B-32/old")  # same dim, other space
    session.commit()
    assert [s for s, _ in search_scenes(session, StubEncoder([1, 0, 0]), "x", k=5)] == [ours]


def test_search_uses_the_default_model_of_an_encoder_that_names_none(session: Session) -> None:
    from rushes.models.clip import MODEL_ID

    class Anonymous:  # no `model_name` attribute at all: the current model is assumed
        dim = 3

        def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
            return np.stack([_unit(1, 0, 0)] * len(texts))

    episode = make_episode(session)
    scene = _scene(session, episode, _unit(1, 0, 0), model=MODEL_ID)
    session.commit()
    assert [s for s, _ in search_scenes(session, Anonymous(), "x")] == [scene]  # type: ignore[arg-type]
