"""Facets of an episode's analysis (docs/plans/00-master.md section 5.5, task 12a).

`base` is the v1 analysis (shots, loudness, motion, speech), `vision` the CLIP vectors, tags and
characters, `sounds` the sound events, `words` the transcript. A step does the facets that are
needed and not done; each one is told from what it left behind, never from a flag.
"""

import contextlib
from typing import Literal

from sqlalchemy import ColumnElement, SQLColumnExpression, exists, select
from sqlalchemy.orm import Session

from rushes.config import Config
from rushes.db.models import Episode, Scene, SceneVector
from rushes.ids import episode_key
from rushes.models.clip import MODEL_ID
from rushes.paths import Paths
from rushes.schemas import AudioFile, read_json

Facet = Literal["base", "vision", "sounds", "words"]


def facets_needed(config: Config, session: Session, episode_id: int) -> set[Facet]:
    """base, vision and sounds always; words for a creator's episode or a spoken series."""
    needed: set[Facet] = {"base", "vision", "sounds"}
    episode = session.get(Episode, episode_id)
    if episode is None:
        return needed
    if episode.creator_id is not None:
        needed.add("words")
    else:
        series = next((s for s in config.series if s.id == episode.series_id), None)
        if series is not None and series.audio == "dialogue_en":
            needed.add("words")
    return needed


def scene_lacks_vector(
    episode_id: SQLColumnExpression[int] | int, model: str = MODEL_ID
) -> ColumnElement[bool]:
    """SQL condition: a scene of the episode has no vector of `model` (vision is not done).

    A vector of another model does not count: its space is not the one search compares in.
    """
    return exists().where(
        Scene.episode_id == episode_id,
        ~exists().where(SceneVector.scene_id == Scene.id, SceneVector.model == model),
    )


def facets_done(session: Session, paths: Paths, episode_id: int) -> set[Facet]:
    """The facets whose result is there: status, vectors, sound events, transcript.

    `vision` is an analyzed episode (`base`) with a vector of the current model for every scene.
    """
    done: set[Facet] = set()
    episode = session.get(Episode, episode_id)
    if episode is None:
        return done
    if episode.status == "analyzed":
        done.add("base")
        if not session.scalar(select(scene_lacks_vector(episode_id))):
            done.add("vision")
    folder = paths.episode_dir(episode_key(episode_id))
    with contextlib.suppress(OSError, ValueError):
        if read_json(AudioFile, folder / "audio.json").events is not None:
            done.add("sounds")
    if (folder / "words.json").is_file():
        done.add("words")
    return done
