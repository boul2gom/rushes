"""Analysis, vision (task 12a): CLIP vectors, tags and characters of every scene.

For each analyzed episode lacking the `vision` facet: the key image of each scene is encoded (in
batches of `analyze.clip_batch`), compared with the tag prompts and with the characters'
reference images, and the vectors, `tags_json` and `characters_json` are written in one
transaction. CLIP loads once per step (R19); the prompts and the references are encoded once per
step too. A scene is matched to its shot by `start_ms` (C40); its image is
`keyframes/<shot.id>.jpg`, read again from clean.mp4 at `Shot.keyframe_ms` when it was lost. A
scene whose vector is of another model counts as lacking (the weights changed): the rows of the
episode are replaced together.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import ColumnElement, exists, select, text
from sqlalchemy.orm import Session

from rushes.analyze.facets import facets_done, scene_lacks_vector
from rushes.analyze.index import model_id, vector_blob
from rushes.analyze.tags import TAG_PROMPTS, character_presence, zero_shot_tags
from rushes.context import Context, Filters
from rushes.db.models import Episode, Scene
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.media.frames import extract_frame
from rushes.models.base import ImageEncoder
from rushes.models.clip import MODEL_ID
from rushes.runner import BaseStep, Outcome
from rushes.schemas import ScenesFile, Shot, read_json

type Vectors = NDArray[np.float32]


def encode_in_batches(encoder: ImageEncoder, images: Sequence[Path], batch: int) -> Vectors:
    """The vectors of `images`, `batch` at a time (one row per image)."""
    parts = [encoder.encode_images(images[i : i + batch]) for i in range(0, len(images), batch)]
    return np.concatenate(parts) if parts else np.zeros((0, encoder.dim), dtype=np.float32)


def _unit(vector: Vectors) -> Vectors:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


class VisionStep(BaseStep):
    name: ClassVar[str] = "vision"
    heavy: ClassVar[bool] = True

    def __init__(self) -> None:
        self._prompts: Vectors | None = None
        self._references: dict[str, dict[str, Vectors]] = {}  # series id -> name -> vector

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Analyzed episodes with a scene lacking its vector; with --force, all with a scene."""
        self._prompts, self._references = None, {}  # encoded once per step, not per run of it
        lacking: ColumnElement[bool] = exists().where(Scene.episode_id == Episode.id)
        if not filters.force:
            lacking = scene_lacks_vector(Episode.id)
        query = select(Episode.id).where(Episode.status == "analyzed", lacking).order_by(Episode.id)
        if filters.episode is not None:
            query = query.where(Episode.id == filters.episode)
        if filters.series is not None:
            query = query.where(Episode.series_id == filters.series)
        if filters.creator is not None:
            query = query.where(Episode.creator_id == filters.creator)
        if filters.account is not None:
            query = query.where(Episode.owner_account_id == filters.account)
        with Session(ctx.engine) as session:
            return list(session.scalars(query))

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = episode_key(item)
        with Session(ctx.engine) as session:  # read, close, then the slow work
            episode = session.get(Episode, item)
            if episode is None:
                return Outcome("skipped", "épisode introuvable")
            if episode.status != "analyzed":
                return Outcome("skipped", f"{key} n'est pas analysé")
            if not force and "vision" in facets_done(session, ctx.paths, item):
                return Outcome("skipped", f"vision déjà faite pour {key}")
            series_id = episode.series_id
            scenes = [
                (row.id, row.start_ms)
                for row in session.execute(
                    select(Scene.id, Scene.start_ms)
                    .where(Scene.episode_id == item)
                    .order_by(Scene.id)
                )
            ]
        if not scenes:
            return Outcome("skipped", f"{key} n'a aucune scène")

        settings = ctx.config.settings.analyze
        images = self._key_images(ctx, item, [start for _, start in scenes])
        encoder = ctx.models.clip()  # loaded once until released (R19)
        if model_id(encoder) != MODEL_ID:  # selection looks for MODEL_ID: never write another
            raise RushesError(
                f"{key} : le modèle chargé est {model_id(encoder)}, la vision écrit {MODEL_ID}"
            )
        vectors = encode_in_batches(encoder, images, settings.clip_batch)
        if vectors.shape != (len(images), encoder.dim):
            raise MediaError(
                f"{key} : CLIP a rendu {vectors.shape} au lieu de {len(images)} vecteurs"
            )
        labels = list(TAG_PROMPTS)
        tags = zero_shot_tags(
            vectors,
            self._prompt_vectors(encoder),
            labels,
            min_cos=settings.tag_min_cos,
            top=settings.tag_top,
        )
        characters = character_presence(
            vectors,
            self._reference_vectors(ctx, encoder, series_id),
            min_cos=settings.character_min_cos,
        )
        model = model_id(encoder)  # what was loaded, not what the settings say
        self._store(ctx, item, scenes, vectors, tags, characters, model=model, dim=encoder.dim)
        return Outcome("done", f"{len(scenes)} scènes")

    def finish(self, ctx: Context) -> None:
        """CLIP is not needed by the steps that follow (FFmpeg): give its memory back."""
        ctx.models.release()

    @staticmethod
    def _key_images(ctx: Context, item: int, starts: Sequence[int]) -> list[Path]:
        """The key image of the shot starting at each of `starts` (matched by start, C40)."""
        key = episode_key(item)
        folder = ctx.paths.episode_dir(key)
        # `clean` and `analyze` both refuse an analyzed episode: only a backup can bring a file back
        hint = f"Restaure le fichier de {folder}/ depuis une sauvegarde."
        try:
            scenes = read_json(ScenesFile, folder / "scenes.json")
        except (OSError, ValueError) as exc:
            raise MediaError(
                f"{key} : scenes.json manquant ou illisible ({exc})", hint=hint
            ) from exc
        if scenes.episode_id != item:
            raise MediaError(f"{key} : scenes.json appartient à l'épisode {scenes.episode_id}")
        by_start: dict[int, Shot] = {shot.start_ms: shot for shot in scenes.shots}
        images: list[Path] = []
        for start in starts:
            shot = by_start.get(start)
            if shot is None:
                raise MediaError(
                    f"{key} : aucun plan de scenes.json ne commence à {start} ms", hint=hint
                )
            image = folder / "keyframes" / f"{shot.id}.jpg"
            if not image.is_file():  # lost since `analyze`: read again where `analyze` read it
                clean_mp4 = folder / "clean.mp4"
                if not clean_mp4.is_file():
                    raise MediaError(
                        f"{key} : image clé {image.name} et clean.mp4 absents", hint=hint
                    )
                extract_frame(clean_mp4, shot.keyframe_ms, image)
            images.append(image)
        return images

    def _prompt_vectors(self, encoder: ImageEncoder) -> Vectors:
        if self._prompts is None:
            self._prompts = encoder.encode_texts(list(TAG_PROMPTS.values()))
        return self._prompts

    def _reference_vectors(
        self, ctx: Context, encoder: ImageEncoder, series_id: str | None
    ) -> dict[str, Vectors]:
        """Per character of the series: the normalised mean of its reference images' vectors."""
        if series_id is None:
            return {}
        if series_id not in self._references:
            series = next((s for s in ctx.config.series if s.id == series_id), None)
            characters = [c for c in series.characters if c.refs] if series is not None else []
            paths = [ctx.paths.config_dir / ref for c in characters for ref in c.refs]
            vectors = encode_in_batches(encoder, paths, ctx.config.settings.analyze.clip_batch)
            means: dict[str, Vectors] = {}
            at = 0
            for character in characters:
                count = len(character.refs)
                mean = _unit(vectors[at : at + count].mean(axis=0))
                at += count
                if float(np.linalg.norm(mean)) > 0:
                    means[character.id] = mean
            self._references[series_id] = means
        return self._references[series_id]

    @staticmethod
    def _store(
        ctx: Context,
        item: int,
        scenes: Sequence[tuple[int, int]],
        vectors: Vectors,
        tags: Sequence[Sequence[str]],
        characters: Sequence[Sequence[str]],
        *,
        model: str,
        dim: int,
    ) -> None:
        """One transaction: the episode's vectors, tags and characters, all or nothing."""
        rows = [
            {
                "i": scene_id,
                "e": item,
                "m": model,
                "d": dim,
                "b": vector_blob(vectors[index]),
                "t": json.dumps(sorted(tags[index])),
                "c": json.dumps(sorted(characters[index])),
            }
            for index, (scene_id, _) in enumerate(scenes)
        ]
        with ctx.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM scene_vectors "
                    "WHERE scene_id IN (SELECT id FROM scenes WHERE episode_id=:e)"
                ),
                {"e": item},
            )
            conn.execute(
                text(
                    "INSERT INTO scene_vectors(scene_id, model, dim, embedding) "
                    "VALUES (:i, :m, :d, :b)"
                ),
                rows,
            )
            for row in rows:
                done = conn.execute(
                    text(
                        "UPDATE scenes SET tags_json=:t, characters_json=:c "
                        "WHERE id=:i AND episode_id=:e"
                    ),
                    row,
                )
                if done.rowcount != 1:  # raising rolls everything back
                    raise RushesError(
                        f"{episode_key(item)} : les scènes ont changé, vision annulée"
                    )
