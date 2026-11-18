"""Analysis, sounds (task 12b): CED sound events of every analyzed episode.

For each analyzed episode lacking the `sounds` facet: the audio of clean.mp4 goes through the
sound tagger (loaded once per step, R19), the AudioSet labels of its windows are grouped into the
project's events (`group_events`), and the events are written to `audio.json` (`events`, an empty
list when there is none) and counted per scene in `scenes.sound_events_json`.

Scenes and audio share the time of clean.mp4 (`analyze` cuts the shots of clean.mp4 and decodes
its audio), so an event overlaps a scene when it intersects [start_ms, end_ms). The facet is told
from `audio.json` alone, so the database rows are written first and the file last: a crash between
them leaves the facet undone and the next run redoes both.
"""

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import ClassVar

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.analyze.facets import facets_done
from rushes.context import Context, Filters
from rushes.core.types import SoundEvent, SoundLabel
from rushes.db.models import Episode, Scene
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.media.audio import decode_pcm16k
from rushes.media.probe import probe
from rushes.runner import BaseStep, Outcome
from rushes.schemas import AudioFile, read_json, write_json

# AudioSet display names (class_labels_indices.csv) -> the project's label
AUDIOSET_GROUPS: Mapping[str, SoundLabel] = {
    **dict.fromkeys(
        (
            "Thump, thud",
            "Bang",
            "Slap, smack",
            "Whack, thwack",
            "Smash, crash",
            "Knock",
            "Crushing",
            "Boing",
        ),
        "impact",
    ),
    **dict.fromkeys(("Explosion", "Boom", "Gunshot, gunfire", "Fireworks"), "explosion"),
    **dict.fromkeys(("Splash, splatter", "Slosh", "Pour"), "splash"),
    **dict.fromkeys(("Screaming", "Yell", "Shout", "Children shouting"), "scream"),
    **dict.fromkeys(
        (
            "Laughter",
            "Giggle",
            "Chuckle, chortle",
            "Belly laugh",
            "Snicker",
            "Baby laughter",
        ),
        "laughter",
    ),
}


def group_events(
    windows: Sequence[tuple[int, Sequence[tuple[str, float]]]],
    *,
    min_score: float = 0.3,
    gap_ms: int = 500,
    window_ms: int = 1000,
) -> list[SoundEvent]:
    """Events of the tagged windows: best score per group, runs of windows merged.

    A window gives each group its best label score (labels outside `AUDIOSET_GROUPS` are ignored)
    and counts for a group when that score is at least `min_score`. Windows of one group whose
    gap (the next start minus the previous end, a window ending `window_ms` after its start) is at
    most `gap_ms` make one event: from the first start to the last start + `window_ms`, scored with
    the best window. Events come in time order.
    """
    best: dict[SoundLabel, list[tuple[int, float]]] = {}
    for start, tags in sorted(windows, key=lambda window: window[0]):
        scores: dict[SoundLabel, float] = {}
        for name, score in tags:
            group = AUDIOSET_GROUPS.get(name)
            if group is not None and score > scores.get(group, float("-inf")):
                scores[group] = score
        for group, score in scores.items():
            if score >= min_score:
                best.setdefault(group, []).append((start, score))
    events: list[SoundEvent] = []
    for group, hits in best.items():
        first, last, top = hits[0][0], hits[0][0], hits[0][1]
        for start, score in hits[1:]:
            if start - (last + window_ms) <= gap_ms:
                last, top = start, max(top, score)
            else:
                events.append(SoundEvent(first, last + window_ms, group, top))
                first, last, top = start, start, score
        events.append(SoundEvent(first, last + window_ms, group, top))
    return sorted(events, key=lambda e: (e.start_ms, e.end_ms, e.label))


def scene_counts(
    events: Sequence[SoundEvent], scenes: Sequence[tuple[int, int, int]]
) -> dict[int, str]:
    """scene id -> `{"impact": 2}` (keys sorted, `{}` when none): the events overlapping it."""
    counts: dict[int, str] = {}
    for scene_id, start, end in scenes:
        found = Counter(e.label for e in events if e.start_ms < end and e.end_ms > start)
        counts[scene_id] = json.dumps(dict(sorted(found.items())))
    return counts


class SoundsStep(BaseStep):
    name: ClassVar[str] = "sounds"
    heavy: ClassVar[bool] = True

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Analyzed episodes without the `sounds` facet; with --force, all analyzed ones."""
        query = select(Episode.id).where(Episode.status == "analyzed").order_by(Episode.id)
        if filters.episode is not None:
            query = query.where(Episode.id == filters.episode)
        if filters.series is not None:
            query = query.where(Episode.series_id == filters.series)
        if filters.creator is not None:
            query = query.where(Episode.creator_id == filters.creator)
        if filters.account is not None:
            query = query.where(Episode.owner_account_id == filters.account)
        with Session(ctx.engine) as session:
            ids = list(session.scalars(query))
            if filters.force:
                return ids
            return [i for i in ids if "sounds" not in facets_done(session, ctx.paths, i)]

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = episode_key(item)
        folder = ctx.paths.episode_dir(key)
        with Session(ctx.engine) as session:  # read, close, then the slow work
            episode = session.get(Episode, item)
            if episode is None:
                return Outcome("skipped", "épisode introuvable")
            if episode.status != "analyzed":
                return Outcome("skipped", f"{key} n'est pas analysé")
            if not force and "sounds" in facets_done(session, ctx.paths, item):
                return Outcome("skipped", f"sons déjà faits pour {key}")
            scenes = [
                (row.id, row.start_ms, row.end_ms)
                for row in session.execute(
                    select(Scene.id, Scene.start_ms, Scene.end_ms)
                    .where(Scene.episode_id == item)
                    .order_by(Scene.start_ms, Scene.id)
                )
            ]
        # `clean` and `analyze` both refuse an analyzed episode: only a backup can bring a file back
        hint = f"Restaure le fichier de {folder}/ depuis une sauvegarde."
        clean_mp4 = folder / "clean.mp4"
        if not clean_mp4.is_file():
            raise MediaError(f"{key} : clean.mp4 absent, sons impossibles", hint=hint)
        try:
            audio = read_json(AudioFile, folder / "audio.json")
        except (OSError, ValueError) as exc:
            raise MediaError(
                f"{key} : audio.json manquant ou illisible ({exc})", hint=hint
            ) from exc
        if audio.episode_id != item:
            raise MediaError(
                f"{key} : audio.json appartient à l'épisode {audio.episode_id}", hint=hint
            )

        events: list[SoundEvent] = []
        if probe(clean_mp4).has_audio:
            pcm = decode_pcm16k(clean_mp4, tmp_dir=ctx.paths.tmp)
            if len(pcm):
                events = ctx.models.sounds().tag(pcm)  # loaded once until released (R19)
        try:  # validated before anything is written: the marker must stay readable
            marker = AudioFile.model_validate(
                {**audio.model_dump(), "events": [dataclasses.asdict(e) for e in events]}
            )
        except ValueError as exc:
            raise MediaError(
                f"{key} : événements sonores invalides ({exc})",
                hint="Le modèle des sons a rendu un événement hors format (rien n'a été écrit).",
            ) from exc
        self._store(ctx, item, scenes, scene_counts(marker.events or [], scenes))
        write_json(marker, folder / "audio.json")  # the marker, last
        return Outcome("done", f"{len(events)} événements")

    def finish(self, ctx: Context) -> None:
        """The tagger is not needed by the steps that follow: give its memory back."""
        ctx.models.release()

    @staticmethod
    def _store(
        ctx: Context, item: int, scenes: Sequence[tuple[int, int, int]], counts: Mapping[int, str]
    ) -> None:
        """One transaction: `sound_events_json` of every scene of the episode, all or nothing."""
        with ctx.engine.begin() as conn:
            for scene_id, _, _ in scenes:
                done = conn.execute(
                    text("UPDATE scenes SET sound_events_json=:j WHERE id=:i AND episode_id=:e"),
                    {"j": counts[scene_id], "i": scene_id, "e": item},
                )
                if done.rowcount != 1:  # raising rolls everything back
                    raise RushesError(f"{episode_key(item)} : les scènes ont changé, sons annulés")
