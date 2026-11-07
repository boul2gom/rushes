"""Analysis v1 (task 5): shots, loudness, motion and speech of a cleaned episode.

Every time is in clean.mp4 time (0 = its start). The slow work (scenedetect, ffmpeg, the VAD,
hashing) runs first; the `scenes` rows and the status are written in one short transaction at the
end (C32). A cleaned episode run again drops its rows and writes them back (R16). Files of an
earlier run are reused only when they belong to the present clean.mp4 (C43).
"""

import math
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import imagehash
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.analyze.loudness import loudness_track
from rushes.analyze.motion import shot_motion
from rushes.analyze.shots import (
    ADAPTIVE_THRESHOLD,
    MIN_CONTENT_VAL,
    MIN_SCENE_LEN,
    WINDOW_WIDTH,
    detect_shots,
)
from rushes.analyze.speech import speech_spans
from rushes.context import Context, Filters
from rushes.core.intervals import Span, overlap_ms
from rushes.db.models import Episode
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.media.audio import decode_pcm16k
from rushes.media.frames import extract_frame
from rushes.media.probe import probe
from rushes.runner import BaseStep, Outcome
from rushes.schemas import AudioFile, CleanFile, LoudPoint, ScenesFile, Shot, read_json, write_json
from rushes.ui import Progress

DETECTOR: dict[str, float] = {
    "adaptive_threshold": ADAPTIVE_THRESHOLD,
    "min_scene_len": float(MIN_SCENE_LEN),
    "window_width": float(WINDOW_WIDTH),
    "min_content_val": MIN_CONTENT_VAL,
}
STAGES = 5


def shot_loudness(points: Sequence[LoudPoint], shot: Span) -> float | None:
    """Mean short-term loudness of the points inside the shot; None when there is none."""
    values = [p.lufs for p in points if shot[0] <= p.t_ms < shot[1]]
    return sum(values) / len(values) if values else None


def speech_ratio(speech: Sequence[Span], shot: Span) -> float:
    """Share (0..1) of the shot covered by speech."""
    covered = sum(overlap_ms(span, shot) for span in speech)
    return min(1.0, covered / (shot[1] - shot[0]))


def keyframe_time(shot: Span, last_frame_ms: int) -> int:
    """Instant of a shot's key image: its middle, but never past `last_frame_ms`.

    The last shot is stretched to the container duration; when the sound outlasts the picture its
    middle can fall after the last frame, where ffmpeg returns nothing. `last_frame_ms` is then
    the limit, so `Shot.keyframe_ms` may be before the middle of the shot (never before its start).
    """
    return max(shot[0], min((shot[0] + shot[1]) // 2, last_frame_ms))


def _cover(spans: Sequence[Span], duration_ms: int) -> list[Span]:
    """Shots clamped to [0, duration]: the first starts at 0, the last ends at the duration."""
    kept = [(s, min(e, duration_ms)) for s, e in sorted(spans) if s < duration_ms]
    kept = [(s, e) for s, e in kept if e > s]
    if not kept:
        return [(0, duration_ms)]
    kept[0] = (0, kept[0][1])
    kept[-1] = (kept[-1][0], duration_ms)
    return kept


class AnalyzeStep(BaseStep):
    name: ClassVar[str] = "analyze"
    heavy: ClassVar[bool] = True

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Cleaned episodes; with --force the analyzed ones too (answered by `run`)."""
        statuses = ("cleaned", "analyzed") if filters.force else ("cleaned",)
        query = select(Episode.id).where(Episode.status.in_(statuses)).order_by(Episode.id)
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
            status, stored_ms = episode.status, episode.duration_ms
        if status == "analyzed":
            return Outcome("skipped", f"déjà analysé : {key} n'est pas analysé de nouveau")
        folder = ctx.paths.episode_dir(key)
        clean_mp4, clean_json = folder / "clean.mp4", folder / "clean.json"
        self._check_clean(item, clean_mp4, clean_json)

        scenes_path, audio_path = folder / "scenes.json", folder / "audio.json"
        shots = self._finished(item, folder, clean_mp4, stored_ms)
        if shots is None or force:
            shots = self._compute(ctx, item, clean_mp4, folder, scenes_path, audio_path)
        self._store(ctx, item, shots)
        return Outcome("done", f"{len(shots)} plans")

    @staticmethod
    def _check_clean(item: int, clean_mp4: Path, clean_json: Path) -> None:
        key = episode_key(item)
        if not (clean_mp4.is_file() and clean_json.is_file()):
            raise MediaError(
                f"{key} : clean.mp4 ou clean.json manquant", hint="Relancer `rushes clean --force`."
            )
        try:
            clean = read_json(CleanFile, clean_json)
        except ValueError as exc:
            raise MediaError(f"{key} : clean.json illisible ({exc})") from exc
        if clean.episode_id != item:
            raise MediaError(f"{key} : clean.json appartient à l'épisode {clean.episode_id}")

    @staticmethod
    def _finished(item: int, folder: Path, clean_mp4: Path, duration_ms: int) -> list[Shot] | None:
        """The shots of a complete earlier run on the present clean.mp4, else None.

        audio.json is written last and is the marker: it must be newer than clean.mp4, and the
        duration recorded by `clean` (from clean.mp4) must be the one scenes.json was made for.
        """
        audio_path = folder / "audio.json"
        try:
            scenes = read_json(ScenesFile, folder / "scenes.json")
            audio = read_json(AudioFile, audio_path)
            if audio_path.stat().st_mtime < clean_mp4.stat().st_mtime:
                return None
        except OSError, ValueError:
            return None
        if scenes.episode_id != item or audio.episode_id != item or not scenes.shots:
            return None
        if scenes.duration_ms != duration_ms:
            return None
        if not all((folder / "keyframes" / f"{s.id}.jpg").is_file() for s in scenes.shots):
            return None
        return scenes.shots

    def _compute(
        self,
        ctx: Context,
        item: int,
        clean_mp4: Path,
        folder: Path,
        scenes_path: Path,
        audio_path: Path,
    ) -> list[Shot]:
        task = f"analyze {episode_key(item)}"
        audio_path.unlink(missing_ok=True)  # the marker: an interrupted recompute is never finished
        tmp_dir = ctx.paths.tmp

        def stage(done: int) -> None:
            ctx.ui.emit(Progress(task, done, STAGES, unit="étapes"))

        info = probe(clean_mp4)
        duration = info.duration_ms
        spans = _cover(detect_shots(clean_mp4), duration)
        stage(1)
        motion = shot_motion(clean_mp4, spans, tmp_dir=tmp_dir)
        stage(2)
        integrated, points = loudness_track(clean_mp4)
        stage(3)
        speech: list[Span] = []
        if info.has_audio:
            speech = speech_spans(ctx.models.vad(), decode_pcm16k(clean_mp4, tmp_dir=tmp_dir))
        stage(4)

        keyframes = folder / "keyframes"
        frame_ms = math.ceil(1000 / info.fps) if info.fps > 0 else 34
        last_frame_ms = max(0, info.video_duration_ms - 2 * frame_ms)  # a frame surely starts here
        shots: list[Shot] = []
        for index, (start, end) in enumerate(spans):
            at = keyframe_time((start, end), last_frame_ms)
            image = keyframes / f"{index}.jpg"
            extract_frame(clean_mp4, at, image)
            with Image.open(image) as picture:
                digest = str(imagehash.phash(picture))
            loudness = shot_loudness(points, (start, end))
            shots.append(
                Shot(
                    id=index,
                    start_ms=start,
                    end_ms=end,
                    keyframe_ms=at,
                    motion=round(motion[index], 4),
                    loudness_lufs=None if loudness is None else round(loudness, 3),
                    speech_ratio=round(speech_ratio(speech, (start, end)), 4),
                    phash=digest,
                )
            )
        for stale in keyframes.glob("*.jpg"):
            if stale.stem not in {str(s.id) for s in shots}:
                stale.unlink()
        write_json(
            ScenesFile(
                version=1, episode_id=item, duration_ms=duration, detector=DETECTOR, shots=shots
            ),
            scenes_path,
        )
        write_json(
            AudioFile(
                version=1,
                episode_id=item,
                integrated_lufs=round(integrated, 3),
                loudness=points,
                speech=speech,
            ),
            audio_path,
        )
        stage(5)
        return shots

    @staticmethod
    def _store(ctx: Context, item: int, shots: Sequence[Shot]) -> None:
        """Short transaction: drop the episode's rows, insert one per shot in order, set status."""
        rows = [
            {
                "e": item,
                "s": shot.start_ms,
                "n": shot.end_ms,
                "l": shot.loudness_lufs,
                "m": shot.motion,
                "p": shot.speech_ratio,
            }
            for shot in shots
        ]
        with ctx.engine.begin() as conn:
            conn.execute(text("DELETE FROM scenes WHERE episode_id=:e"), {"e": item})
            conn.execute(
                text(
                    "INSERT INTO scenes(episode_id, start_ms, end_ms, loudness, motion, speech) "
                    "VALUES (:e, :s, :n, :l, :m, :p)"
                ),
                rows,
            )
            done = conn.execute(
                text("UPDATE episodes SET status='analyzed' WHERE id=:e AND status='cleaned'"),
                {"e": item},
            )
            if done.rowcount != 1:  # raising rolls the rows back
                raise RushesError(f"{episode_key(item)} : n'est plus nettoyé, analyse annulée")
