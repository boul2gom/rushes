"""Cartoon cleaning v1 (R45): remove only what touches an edge, then normalize to clean.mp4.

Removed at an edge: black and silence over 2 s, a frozen title card that starts in the first 20 s
and touches the start or a removed zone, opening and closing credits found from the series'
reference images. Nothing inside the episode is ever removed. Creator episodes are only
normalized here (SponsorBlock and autocut come with task 24).
"""

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import ClassVar

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.clean.borders import border_spans, kept_range
from rushes.clean.intro import match_reference_frames
from rushes.context import Context, Filters
from rushes.core.intervals import Span, merge, subtract
from rushes.db.models import Episode
from rushes.errors import MediaError
from rushes.fsutil import atomic_path
from rushes.ids import episode_key
from rushes.media.detect import detect_black, detect_freeze, detect_silence
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.probe import ProbeInfo, probe
from rushes.runner import BaseStep, Outcome
from rushes.schemas import CleanFile, RemovedKind, read_json, write_json
from rushes.ui import Progress

MIN_BORDER_MS = 2000  # black and silence: strictly longer than this
MIN_FREEZE_MS = 1500
MIN_BLACK_SEEN_MS = 500  # shorter blacks are not worth telling from a frozen picture
TITLE_CARD_WINDOW_MS = 20_000
EPS_MS = 100  # "touching" an edge or a removed zone
REFERENCE_EPS_MS = 500  # one sample of the reference-frame search (2 fps)
MAX_PASSES = 5  # chaining stops when nothing changes; this only bounds a pathological loop
VIDEO_FILTER = "scale=trunc(iw*sar/2)*2:ih,setsar=1,fps=30"
SILENT_TRACK = "anullsrc=r=48000:cl=stereo"

type Removed = list[tuple[int, int, RemovedKind]]


def _edges(removed: Sequence[Span], duration_ms: int) -> Span:
    """(end of the zone chained to the start, start of the zone chained to the end)."""
    zones = merge(removed)
    prefix = zones[0][1] if zones and zones[0][0] <= 0 else 0
    suffix = zones[-1][0] if zones and zones[-1][1] >= duration_ms else duration_ms
    return prefix, suffix


def _add(removed: Removed, spans: Iterable[Span], kind: RemovedKind) -> None:
    """Append the parts of `spans` not already removed (the entries never overlap)."""
    for span in spans:
        for start, end in subtract(span, [(s, e) for s, e, _ in removed]):
            removed.append((start, end, kind))


def _chain_front(
    removed: Removed,
    duration_ms: int,
    spans: Sequence[Span],
    kind: RemovedKind,
    *,
    eps_ms: int,
    until_ms: int | None = None,
) -> None:
    """Remove spans that start at the start or where a removed zone chained to it ends."""
    for start, end in sorted(spans):
        prefix, _ = _edges([(s, e) for s, e, _ in removed], duration_ms)
        if start > prefix + eps_ms or (until_ms is not None and start >= until_ms):
            continue
        if end - prefix > eps_ms:
            _add(removed, [(prefix, end)], kind)


def _chain_back(
    removed: Removed, duration_ms: int, spans: Sequence[Span], kind: RemovedKind, *, eps_ms: int
) -> None:
    for start, end in sorted(spans, key=lambda s: s[1], reverse=True):
        _, suffix = _edges([(s, e) for s, e, _ in removed], duration_ms)
        if end >= suffix - eps_ms and suffix - start > eps_ms:
            _add(removed, [(start, suffix)], kind)


def _long(spans: Sequence[Span]) -> list[Span]:
    return [(s, e) for s, e in spans if e - s > MIN_BORDER_MS]


def cartoon_removed(ctx: Context, source: Path, series_id: str, info: ProbeInfo) -> Removed:
    """Zones to cut at the edges: black, silence, credits and title card, chained until stable.

    A zone counts when it touches an edge or a zone already removed from that edge, whatever the
    order in which they were found (a card, then black, then credits all go). Black and freeze
    are video facts: their end is compared with the video stream's, with one frame of slack.
    """
    duration, video_end = info.duration_ms, info.video_duration_ms
    frame_ms = math.ceil(1000 / info.fps) if info.fps > 0 else 34
    video_eps = EPS_MS + frame_ms
    tail_eps = video_eps + max(0, duration - video_end)  # the container outlasts the video

    blacks = detect_black(source, min_ms=MIN_BLACK_SEEN_MS)
    silences = (
        detect_silence(source, min_ms=MIN_BORDER_MS, duration_ms=duration) if info.has_audio else []
    )
    # black frames are frozen too: only what is frozen and not black can be a title card
    frozen = detect_freeze(source, min_ms=MIN_FREEZE_MS, duration_ms=video_end)
    freezes = [piece for span in frozen for piece in subtract(span, blacks)]
    freezes = [(s, e) for s, e in freezes if e - s >= MIN_FREEZE_MS]
    series = ctx.config.series_by_id(series_id)
    refs = [ctx.paths.config_dir / r for r in series.intro_outro.reference_frames] if series else []
    matches = (
        match_reference_frames(source, refs, tmp_dir=ctx.paths.tmp, duration_ms=duration)
        if refs
        else []
    )

    removed: Removed = []
    edge_blacks = border_spans(blacks, video_end, min_ms=MIN_BORDER_MS, eps_ms=video_eps)
    _add(removed, [(s, duration if e >= video_end else e) for s, e in edge_blacks], "black")
    _add(removed, border_spans(silences, duration, min_ms=MIN_BORDER_MS), "silence")
    for _ in range(MAX_PASSES):
        before = sorted(removed)
        _chain_front(removed, duration, _long(blacks), "black", eps_ms=video_eps)
        _chain_back(removed, duration, _long(blacks), "black", eps_ms=tail_eps)
        _chain_front(removed, duration, _long(silences), "silence", eps_ms=EPS_MS)
        _chain_back(removed, duration, _long(silences), "silence", eps_ms=EPS_MS)
        _chain_front(removed, duration, matches, "intro", eps_ms=REFERENCE_EPS_MS)
        _chain_back(removed, duration, matches, "outro", eps_ms=REFERENCE_EPS_MS)
        _chain_front(
            removed,
            duration,
            freezes,
            "title_card",
            eps_ms=video_eps,
            until_ms=TITLE_CARD_WINDOW_MS,
        )
        if sorted(removed) == before:
            break
    return removed


class CleanStep(BaseStep):
    name: ClassVar[str] = "clean"
    heavy: ClassVar[bool] = True

    def __init__(self, *, preset: str = "veryfast") -> None:
        self.preset = preset

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Imported episodes; with --force every matching one, analyzed ones answered by `run`."""
        statuses = ("imported", "cleaned", "analyzed") if filters.force else ("imported",)
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
        with Session(ctx.engine) as session:  # read, close, then the slow work
            episode = session.get(Episode, item)
            if episode is None:
                return Outcome("skipped", "épisode introuvable")
            file_path, series_id, status = episode.file_path, episode.series_id, episode.status
        if status == "analyzed":  # redoing it would orphan its scenes, segments and posts
            return Outcome(
                "skipped", f"déjà analysé : {episode_key(item)} n'est pas nettoyé de nouveau"
            )
        source = ctx.paths.data_dir / file_path
        folder = ctx.paths.episode_dir(episode_key(item))
        clean_mp4, clean_json = folder / "clean.mp4", folder / "clean.json"

        if not force and self._finished(item, clean_mp4, clean_json):
            self._mark_cleaned(ctx, item, probe(clean_mp4).duration_ms)
            return Outcome("done", "fichiers déjà produits")

        info = probe(source)
        removed: Removed = []
        if series_id is not None:
            removed = cartoon_removed(ctx, source, series_id, info)
        try:
            kept = kept_range(info.duration_ms, [(s, e) for s, e, _ in removed])
        except ValueError as exc:
            raise MediaError(f"{episode_key(item)} : {exc}") from exc

        self._encode(ctx, item, source, info, kept, clean_mp4)
        write_json(
            CleanFile(
                version=1,
                episode_id=item,
                source_duration_ms=info.duration_ms,
                kept=kept,
                removed=sorted(removed),
            ),
            clean_json,
        )
        self._mark_cleaned(ctx, item, probe(clean_mp4).duration_ms)
        gone = sum(e - s for s, e, _ in removed)
        return Outcome(
            "done", f"{(kept[1] - kept[0]) / 1000:.1f} s gardées, {gone / 1000:.1f} s retirées"
        )

    @staticmethod
    def _finished(item: int, clean_mp4: Path, clean_json: Path) -> bool:
        """clean.mp4 is written before clean.json: a readable json means both are complete."""
        if not (clean_mp4.is_file() and clean_json.is_file()):
            return False
        try:
            return read_json(CleanFile, clean_json).episode_id == item
        except ValueError:
            return False

    def _encode(
        self, ctx: Context, item: int, source: Path, info: ProbeInfo, kept: Span, target: Path
    ) -> None:
        length_ms = kept[1] - kept[0]
        args: list[str | Path] = []
        if kept != (0, info.duration_ms):
            args += ["-ss", f"{kept[0] / 1000:.3f}", "-t", f"{length_ms / 1000:.3f}"]
        args += ["-i", source]
        if not info.has_audio:
            args += ["-f", "lavfi", "-i", SILENT_TRACK]
        args += ["-map", "0:v:0", "-map", "0:a:0" if info.has_audio else "1:a:0"]
        args += ["-vf", VIDEO_FILTER, "-c:v", "libx264", "-preset", self.preset, "-crf", "16"]
        args += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        if not info.has_audio:
            args.append("-shortest")
        task = f"clean {episode_key(item)}"

        def progress(percent: int) -> None:
            ctx.ui.emit(Progress(task, percent, 100, unit="%"))  # the console throttles it

        with atomic_path(target) as tmp:
            run_ffmpeg(
                [*args, "-movflags", "+faststart", "-f", "mp4", tmp],
                duration_ms=length_ms,
                on_progress=progress,
            )

    @staticmethod
    def _mark_cleaned(ctx: Context, item: int, duration_ms: int) -> None:
        with ctx.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE episodes SET status='cleaned', duration_ms=:d "
                    "WHERE id=:i AND status IN ('imported', 'cleaned')"
                ),
                {"d": duration_ms, "i": item},
            )
