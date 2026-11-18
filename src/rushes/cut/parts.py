"""Parts of an analyzed episode (task 6): dynamic-programming partition, one 'part' segment each.

Reads `scenes.json` and `audio.json` (all times in clean.mp4 time), picks cut points among the
shot starts (plus quiet points when those are not enough), partitions the episode into parts of
the family's duration range and writes the segments in one short transaction. A part someone
already uses (assigned, in a compilation, hooked, posted) is never touched again (master 5.3).
"""

import json
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from rushes.config import CutSettings
from rushes.context import Context, Filters
from rushes.core.dp import partition, shift_weak_starts
from rushes.core.intervals import Span, overlap_ms
from rushes.core.rng import derive_seed, draw_target_ms
from rushes.core.scoring import cut_point_costs, quiet_points
from rushes.core.timecode import s_to_ms
from rushes.db.models import Episode, Segment
from rushes.errors import MediaError, RushesError
from rushes.ids import episode_key
from rushes.runner import BaseStep, Outcome
from rushes.schemas import AudioFile, ScenesFile, Shot, read_json

FAMILY = "monetizable"  # `long` is for best-of and compilations (spec, Familles de durée)
MOTION_WINDOW_MS = 2000  # the 2 s after a cut (spec, Partition en parties, step 5)
LOUDNESS_WINDOW_MS = 3000  # LoudPoint.t_ms is the END of this window (C42)

# A part someone already uses: not 'proposed' any more, or referenced by a later table.
_USED = (
    "status <> 'proposed' "
    "OR EXISTS (SELECT 1 FROM assignments a WHERE a.segment_id = segments.id) "
    "OR EXISTS (SELECT 1 FROM compilation_items c WHERE c.segment_id = segments.id) "
    "OR EXISTS (SELECT 1 FROM hooks h WHERE h.segment_id = segments.id) "
    "OR EXISTS (SELECT 1 FROM posts p WHERE p.segment_id = segments.id)"
)


@dataclass(frozen=True)
class Part:
    start_ms: int
    end_ms: int
    target_ms: int
    score: float
    phash: list[str]


def centre_loudness(audio: AudioFile) -> list[tuple[int, float]]:
    """(centre time, LUFS): `t_ms` ends the 3 s window, so its centre is 1.5 s earlier (C44)."""
    return [(p.t_ms - LOUDNESS_WINDOW_MS // 2, p.lufs) for p in audio.loudness]


def motion_after_fn(shots: Sequence[Shot]) -> Callable[[int], float]:
    """`motion_after(p)`: mean shot motion over [p, p + 2 s], weighted by overlap; 0 if none."""
    ordered = sorted(shots, key=lambda s: s.start_ms)
    starts = [s.start_ms for s in ordered]

    def motion_after(p: int) -> float:
        window = (p, p + MOTION_WINDOW_MS)
        weight = total = 0.0
        for shot in ordered[max(0, bisect_right(starts, p) - 1) :]:
            if shot.start_ms >= window[1]:
                break
            share = overlap_ms((shot.start_ms, shot.end_ms), window)
            weight += share
            total += share * shot.motion
        return total / weight if weight else 0.0

    return motion_after


def plan_parts(
    *,
    episode_id: int,
    shots: Sequence[Shot],
    audio: AudioFile,
    duration_ms: int,
    bounds_s: tuple[float, float],
    s: CutSettings,
) -> list[Part]:
    """The parts of an episode; [] when no admissible partition exists."""
    min_ms, max_ms = s_to_ms(bounds_s[0]), s_to_ms(bounds_s[1])
    targets = [
        draw_target_ms(derive_seed(episode_id, "cut", k), *bounds_s)
        for k in range(duration_ms // min_ms + 1)
    ]
    shot_starts = sorted({sh.start_ms for sh in shots if 0 < sh.start_ms < duration_ms})
    base = [0, *shot_starts, duration_ms]
    loudness = centre_loudness(audio)
    motion_after = motion_after_fn(shots)
    peaks_ms: list[int] = []  # task 13: heatmap_peaks_ms(video_signals)

    def attempt(points: list[int], penalised: set[int]) -> tuple[list[Span], dict[int, float]]:
        costs = cut_point_costs(
            points,
            loudness=loudness,
            speech=audio.speech,
            peaks_ms=peaks_ms,
            motion_after=motion_after,
            s=s,
        )
        costs = [
            c + s.quiet_point_penalty if p in penalised else c
            for p, c in zip(points, costs, strict=True)
        ]
        spans = partition(
            points,
            costs,
            duration_ms=duration_ms,
            targets_ms=targets,
            min_ms=min_ms,
            max_ms=max_ms,
            target_weight=s.target_weight,
        )
        return spans, dict(zip(points, costs, strict=True))

    spans, cost_of = attempt(base, set())
    if not spans:  # no shot change to cut at: add the quiet points and try once more
        quiet = set(quiet_points(loudness, step_ms=s.quiet_point_step_ms, duration_ms=duration_ms))
        extra = quiet - set(base)
        if extra:
            spans, cost_of = attempt(sorted({*base, *extra}), extra)
    if not spans:
        return []
    spans = shift_weak_starts(
        spans,
        shot_starts_ms=shot_starts,
        cost_of=cost_of,
        motion_after=motion_after,
        min_ms=min_ms,
        max_ms=max_ms,
        motion_min=s.start_motion_min,
        shift_ms=s.start_shift_ms,
    )
    return [
        Part(
            start_ms=start,
            end_ms=end,
            target_ms=targets[k],
            score=1 / (1 + max(0.0, cost_of[end])),
            phash=[
                sh.phash for sh in shots if overlap_ms((sh.start_ms, sh.end_ms), (start, end)) > 0
            ],
        )
        for k, (start, end) in enumerate(spans)
    ]


class CutStep(BaseStep):
    name: ClassVar[str] = "cut"

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Analyzed episodes without a part; with --force the ones with parts too.

        A community episode is only a signal (R15): never cut.
        """
        query = (
            select(Episode.id)
            .where(Episode.status == "analyzed", Episode.provenance != "community")
            .order_by(Episode.id)
        )
        if not filters.force:
            has_part = exists().where(Segment.episode_id == Episode.id, Segment.kind == "part")
            query = query.where(~has_part)
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
        with Session(ctx.engine) as session:  # read, close, then compute
            episode = session.get(Episode, item)
            if episode is None:
                return Outcome("skipped", "épisode introuvable")
            status, stored_ms = episode.status, episode.duration_ms
        if status != "analyzed":
            return Outcome("skipped", f"{key} n'est pas analysé")
        with ctx.engine.connect() as conn:
            old = list(
                conn.execute(
                    text("SELECT id FROM segments WHERE episode_id=:e AND kind='part'"), {"e": item}
                ).scalars()
            )
            used = conn.execute(
                text(
                    "SELECT count(*) FROM segments "
                    f"WHERE episode_id=:e AND kind='part' AND ({_USED})"
                ),
                {"e": item},
            ).scalar_one()
        if old and not force:
            return Outcome("skipped", f"déjà découpé : {key} n'est pas découpé de nouveau")
        if used:
            return Outcome("skipped", f"déjà attribué : les parties de {key} sont intouchables")

        scenes, audio = self._load(ctx, item, stored_ms)
        family = ctx.config.accounts.families.get(FAMILY)
        if family is None:
            raise RushesError(f"famille {FAMILY} absente de accounts.yaml")
        if stored_ms < s_to_ms(family.min_s):
            if old:
                self._store(ctx, item, [], old)  # --force: no part left out of the bounds (R6)
            return Outcome(
                "warned", f"épisode trop court pour la famille {FAMILY} ({stored_ms / 1000:g} s)"
            )
        parts = plan_parts(
            episode_id=item,
            shots=scenes.shots,
            audio=audio,
            duration_ms=stored_ms,
            bounds_s=(family.min_s, family.max_s),
            s=ctx.config.settings.cut,
        )
        if not parts:
            if old:
                self._store(ctx, item, [], old)  # --force: no part left out of the bounds (R6)
            return Outcome("warned", "aucun point de coupe admissible")
        self._store(ctx, item, parts, old)
        return Outcome("done", f"{len(parts)} parties")

    @staticmethod
    def _load(ctx: Context, item: int, duration_ms: int) -> tuple[ScenesFile, AudioFile]:
        key = episode_key(item)
        folder: Path = ctx.paths.episode_dir(key)
        try:
            scenes = read_json(ScenesFile, folder / "scenes.json")
            audio = read_json(AudioFile, folder / "audio.json")
        except (OSError, ValueError) as exc:
            raise MediaError(
                f"{key} : scenes.json ou audio.json illisible ({exc})",
                hint="Relancer `rushes clean --force` puis `rushes analyze`.",
            ) from exc
        if scenes.episode_id != item or audio.episode_id != item:
            raise MediaError(f"{key} : les fichiers d'analyse appartiennent à un autre épisode")
        if scenes.duration_ms != duration_ms:
            raise MediaError(
                f"{key} : scenes.json ({scenes.duration_ms} ms) ne correspond plus à l'épisode",
                hint="Relancer `rushes clean --force` puis `rushes analyze`.",
            )
        return scenes, audio

    @staticmethod
    def _store(ctx: Context, item: int, parts: Sequence[Part], old: Sequence[int]) -> None:
        """Short transaction: drop the unused earlier parts, insert one row per part (C32).

        With no part to write it only drops the old ones (--force found no partition)."""
        rows = [
            {
                "e": item,
                "s": part.start_ms,
                "n": part.end_ms,
                "f": FAMILY,
                "t": part.target_ms,
                "d": derive_seed(item, "cut"),
                "c": part.score,
                "h": json.dumps(part.phash),
            }
            for part in parts
        ]
        with ctx.engine.begin() as conn:
            guard = conn.execute(  # takes the write lock first: the checks below stay true
                text("UPDATE episodes SET status='analyzed' WHERE id=:e AND status='analyzed'"),
                {"e": item},
            )
            if guard.rowcount != 1:
                raise RushesError(f"{episode_key(item)} : n'est plus analysé, découpe annulée")
            present = conn.execute(
                text("SELECT id FROM segments WHERE episode_id=:e AND kind='part'"), {"e": item}
            ).scalars()
            if sorted(present) != sorted(old):
                raise RushesError(f"{episode_key(item)} : parties modifiées pendant la découpe")
            for segment_id in old:
                gone = conn.execute(
                    text(f"DELETE FROM segments WHERE id=:i AND NOT ({_USED})"), {"i": segment_id}
                )
                if gone.rowcount != 1:  # raising rolls everything back
                    raise RushesError(f"{episode_key(item)} : une partie vient d'être utilisée")
            if rows:
                conn.execute(
                    text(
                        "INSERT INTO segments(episode_id, kind, start_ms, end_ms, family, "
                        "target_ms, seed, score, status, phash_json) VALUES "
                        "(:e, 'part', :s, :n, :f, :t, :d, :c, 'proposed', :h)"
                    ),
                    rows,
                )
            written = conn.execute(
                text("SELECT count(*) FROM segments WHERE episode_id=:e AND kind='part'"),
                {"e": item},
            ).scalar_one()
            if written != len(rows):
                raise RushesError(
                    f"{episode_key(item)} : {written} parties écrites sur {len(rows)}"
                )
