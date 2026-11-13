"""Cartoon cleaning (R45): remove only what touches an edge, then normalize to clean.mp4.

Removed at an edge: black and silence over 2 s, a frozen title card that starts in the first 20 s
and touches the start or a removed zone, opening and closing credits found from the series'
reference images and from the audio fingerprint of its theme. Nothing inside the episode is ever
removed. Creator episodes are only normalized here (SponsorBlock and autocut come with task 24).

A cartoon compilation (the theme plays several times, or a long video split on its blacks) is
cut into child episodes first: the parent becomes `split` and is never analyzed, each child shares
the parent's file and cleans its own portion (`CleanFile.span`) in the same run. The burned-in logo
corner of every cleaned episode is stored for R34.
"""

import dataclasses
import math
import shutil
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.clean.borders import border_spans, kept_range
from rushes.clean.intro import match_reference_frames
from rushes.clean.logo import detect_logo
from rushes.clean.splitter import black_boundaries, split_spans
from rushes.config import ConfigError, SeriesConfig
from rushes.context import Context, Filters
from rushes.core.intervals import Span, merge, subtract
from rushes.db.models import Episode
from rushes.errors import MediaError
from rushes.fsutil import atomic_path
from rushes.ids import episode_key
from rushes.media.audio import SAMPLE_RATE as PCM_RATE
from rushes.media.audio import decode_pcm16k
from rushes.media.detect import detect_black, detect_freeze, detect_silence
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.fingerprint import band_spectrogram, slice_correlation, theme_matches
from rushes.media.probe import ProbeInfo, probe
from rushes.media.verify import SAMPLE_RATE, SILENT_TRACK
from rushes.runner import BaseStep, Outcome
from rushes.schemas import CleanFile, RemovedKind, read_json, write_json
from rushes.ui import Message, Progress, StepEvent

MIN_BORDER_MS = 2000  # black and silence: strictly longer than this
MIN_FREEZE_MS = 1500
MIN_BLACK_SEEN_MS = 500  # shorter blacks are not worth telling from a frozen picture
TITLE_CARD_WINDOW_MS = 20_000
EPS_MS = 100  # "touching" an edge or a removed zone
REFERENCE_EPS_MS = 500  # one sample of the reference-frame search (2 fps)
THEME_EDGE_MS = 2000  # a theme this close to an edge is the episode's own opening or closing
THEME_CONFIRM_MS = 5000  # a reference image may show this long after the theme's end
SAME_PLACE_CORR = 0.02  # two variants this close in correlation: the longer one is the generic
SNAP_MIN_SLICE_MS = 1500  # less theme than this after a zone is no evidence that it goes on
MAX_PASSES = 5  # chaining stops when nothing changes; this only bounds a pathological loop
VIDEO_FILTER = "scale=trunc(iw*sar/2)*2:ih,setsar=1,fps=30"

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


@dataclass(frozen=True)
class ThemePeak:
    """Where a variant of the series theme plays, with the length of the variant that matched."""

    start: int  # ms of the whole file
    length_ms: int  # the matching variant's own length
    corr: float
    variant: int = 0  # index in the series' list of theme files
    end_ms: int | None = None  # where the generic really ends (snapped); None: start + length

    @property
    def opening_end(self) -> int:
        return self.start + self.length_ms if self.end_ms is None else self.end_ms


@dataclass(frozen=True)
class Scan:
    """What one pass over a source (or a part of it) found, in the time of the whole file."""

    blacks: list[Span]
    silences: list[Span]
    themes: list[ThemePeak]  # confirmed peaks of the series theme, in time order


class ThemeLoader:
    """Decodes each theme variant of a series once per run; an unusable one is reported once."""

    def __init__(self) -> None:
        self._themes: dict[Path, NDArray[np.float32] | None] = {}

    def load(self, ctx: Context, series: SeriesConfig | None) -> list[NDArray[np.float32]]:
        """The usable variants (the others are warned about and left out)."""
        if series is None:
            return []
        found: list[NDArray[np.float32]] = []
        for name in series.intro_outro.theme_paths:
            path = ctx.paths.config_dir / name
            if path not in self._themes:
                self._themes[path] = self._decode(ctx, path)
            theme = self._themes[path]
            if theme is not None:
                found.append(theme)
        return found

    @staticmethod
    def _decode(ctx: Context, path: Path) -> NDArray[np.float32] | None:
        theme: NDArray[np.float32] | None = None
        reason = ""
        try:
            theme = decode_pcm16k(path, tmp_dir=ctx.paths.tmp)
            if theme.size < PCM_RATE:
                reason = "moins d'une seconde"
        except (MediaError, OSError, ValueError) as exc:
            reason = (str(exc).splitlines() or [type(exc).__name__])[0]
        if reason or theme is None:
            ctx.ui.emit(
                Message(
                    "warn",
                    f"thème audio inutilisable ({path.name} : {reason})",
                    hint="Ce générique n'est pas cherché par l'audio : corriger theme_audio.",
                )
            )
            return None
        return theme


def _to_local(spans: Iterable[Span], span: Span | None) -> list[Span]:
    """Spans of the file as spans of `span` (clipped to it); unchanged without one."""
    if span is None:
        return list(spans)
    start, end = span
    clipped = ((max(s, start) - start, min(e, end) - start) for s, e in spans)
    return [(s, e) for s, e in clipped if e > s]


def _shifted(spans: Iterable[Span], by_ms: int) -> list[Span]:
    return [(s + by_ms, e + by_ms) for s, e in spans]


def _one_peak_per_place(found: Sequence[ThemePeak]) -> list[ThemePeak]:
    """Hits of different variants that overlap are one peak: the strongest, with its own length.

    When the clashing hits are within SAME_PLACE_CORR of each other (a short and an extended
    version of the same generic) the longer variant wins. Hits of the same variant never merge
    (the fingerprint already keeps them a theme apart).
    """
    kept: list[ThemePeak] = []
    for peak in sorted(found, key=lambda p: (-p.corr, p.start, p.variant)):
        clashes = [
            k
            for k in kept
            if k.variant != peak.variant
            and peak.start < k.start + k.length_ms
            and k.start < peak.start + peak.length_ms
        ]
        if not clashes:
            kept.append(peak)
        elif (
            len(clashes) == 1
            and clashes[0].corr - peak.corr < SAME_PLACE_CORR
            and peak.length_ms > clashes[0].length_ms
        ):
            kept[kept.index(clashes[0])] = peak
    return sorted(kept, key=lambda p: p.start)


def _confirmed(
    ctx: Context, source: Path, series: SeriesConfig, peaks: list[ThemePeak], end_ms: int
) -> list[ThemePeak]:
    """Peaks kept: without reference images all of them; else the sure ones (a different era may
    have another card) and those with an image showing between the peak and the end of the theme
    (plus THEME_CONFIRM_MS). A correlation is clipped to 1.0: an exact match is always sure."""
    refs = [ctx.paths.config_dir / r for r in series.intro_outro.reference_frames]
    if not refs:
        return peaks
    sure = ctx.config.settings.clean.sure_threshold
    return [
        p
        for p in peaks
        if p.corr >= sure
        or match_reference_frames(
            source,
            refs,
            tmp_dir=ctx.paths.tmp,
            duration_ms=end_ms,
            windows=[(p.start, min(p.start + p.length_ms + THEME_CONFIRM_MS, end_ms))],
        )
    ]


type Zone = tuple[int, int, int]  # (start, end, rank): 0 black + silence, 1 black, 2 silence


def _zone_candidates(blacks: Sequence[Span], silences: Sequence[Span]) -> list[Zone]:
    """Where a generic may end: blacks of at least MIN_BLACK_SEEN_MS, silences of at least
    MIN_BORDER_MS, whatever the scan's own minimums (the answer must not depend on how the
    episode was scanned). Overlapping zones are one: a black with a silence ranks above a black
    alone above a silence alone, and a zone with a black is bounded by its black."""
    items = sorted(
        [(s, e, True) for s, e in blacks if e - s >= MIN_BLACK_SEEN_MS]
        + [(s, e, False) for s, e in silences if e - s >= MIN_BORDER_MS]
    )
    clusters: list[list[tuple[int, int, bool]]] = []
    for item in items:
        if clusters and item[0] < max(e for _, e, _ in clusters[-1]):
            clusters[-1].append(item)
        else:
            clusters.append([item])
    zones: list[Zone] = []
    for cluster in clusters:
        black = [(s, e) for s, e, is_black in cluster if is_black]
        quiet = [(s, e) for s, e, is_black in cluster if not is_black]
        if black:
            zones.append((min(s for s, _ in black), max(e for _, e in black), 0 if quiet else 1))
        else:
            zones.append((min(s for s, _ in quiet), max(e for _, e in quiet), 2))
    return zones


def _snapped_end(
    start: int,
    nominal_end: int,
    limit: int,
    zones: Sequence[Zone],
    snap_ms: int,
    *,
    length_ms: int,
    continues: Callable[[int], bool],
) -> int:
    """Where a generic that starts at `start` and should end at `nominal_end` really ends.

    Another era's generic is a few seconds shorter or longer. Only zones (black/silence, see
    `_zone_candidates`) within `snap_ms` of the nominal end are looked at, and only those that
    start once the generic has really begun (half of it, and MIN_BORDER_MS, after `start`):
    1. a zone ending before or at the nominal end ends the generic if the theme no longer plays
       after it (`continues(zone end)` is false): the earliest such zone. A fade inside the
       generic, where the theme goes on, is skipped;
    2. else the nearest zone that contains or follows the nominal end (a black, then a silence,
       then the one with both first on a tie);
    3. else the nominal end.
    Never past `limit` (the next peak or the end of the episode); 0 disables snapping.
    """
    if snap_ms <= 0:
        return nominal_end
    begun = start + max(MIN_BORDER_MS, length_ms // 2)
    near = [
        z
        for z in zones
        if z[0] >= begun and z[0] <= nominal_end + snap_ms and z[1] >= nominal_end - snap_ms
    ]
    ended = [z for z in near if z[1] <= nominal_end and not continues(z[1])]
    if ended:
        end = min(ended, key=lambda z: z[1])[1]
    else:
        later = [z for z in near if z[1] > nominal_end]
        if not later:
            return nominal_end
        end = min(later, key=lambda z: (max(0, z[0] - nominal_end), z[2], -z[1]))[1]
    end = min(end, limit)
    return end if end > start else nominal_end


def _with_snapped_ends(
    peaks: list[ThemePeak],
    *,
    track_spec: NDArray[np.float32],
    theme_specs: Sequence[NDArray[np.float32]],
    zones: Sequence[Zone],
    origin_ms: int,
    end_ms: int,
    hop_ms: int,
    threshold: float,
    snap_ms: int,
) -> list[ThemePeak]:
    """The peaks with the real end of their generic (`ThemePeak.end_ms`)."""
    snapped: list[ThemePeak] = []
    for i, peak in enumerate(peaks):
        spec_h = theme_specs[peak.variant]
        peak_frame = (peak.start - origin_ms) // hop_ms

        def continues(
            zone_end: int, spec_h: NDArray[np.float32] = spec_h, peak_frame: int = peak_frame
        ) -> bool:
            """Does the track, from `zone_end` on, still play the theme (at least 1.5 s of it)?"""
            frame = -(-(zone_end - origin_ms) // hop_ms)  # first frame after the zone
            inside = frame - peak_frame  # the same instant in the theme
            used = min(spec_h.shape[1] - inside, track_spec.shape[1] - frame)
            if inside < 0 or used * hop_ms < SNAP_MIN_SLICE_MS:
                return False  # no evidence that the theme goes on
            return (
                slice_correlation(
                    track_spec[:, frame : frame + used], spec_h[:, inside : inside + used]
                )
                >= threshold
            )

        limit = peaks[i + 1].start if i + 1 < len(peaks) else end_ms
        end = _snapped_end(
            peak.start,
            min(peak.start + peak.length_ms, end_ms),
            limit,
            zones,
            snap_ms,
            length_ms=peak.length_ms,
            continues=continues,
        )
        snapped.append(dataclasses.replace(peak, end_ms=end))
    return snapped


def scan_source(
    ctx: Context,
    source: Path,
    series: SeriesConfig | None,
    info: ProbeInfo,
    *,
    silence_min_ms: int,
    black_min_ms: int = MIN_BLACK_SEEN_MS,
    span: Span | None = None,
    themes: ThemeLoader | None = None,
) -> Scan:
    """Black, silence and theme peaks of the file, or of `span` of it (always in file time)."""
    start, end = span if span is not None else (0, info.duration_ms)
    blacks = _shifted(detect_black(source, min_ms=black_min_ms, span=span), start)
    silences = _shifted(
        detect_silence(source, min_ms=silence_min_ms, duration_ms=end - start, span=span)
        if info.has_audio
        else [],
        start,
    )
    peaks: list[ThemePeak] = []
    variants = (themes or ThemeLoader()).load(ctx, series)
    if variants and series is not None and info.has_audio:
        settings = ctx.config.settings.clean
        track = decode_pcm16k(source, span=span, tmp_dir=ctx.paths.tmp)
        track_spec = band_spectrogram(track, hop_ms=settings.theme_hop_ms)  # once for every variant
        theme_specs = [band_spectrogram(t, hop_ms=settings.theme_hop_ms) for t in variants]
        found = [
            ThemePeak(start + at, theme.size * 1000 // PCM_RATE, corr, index)
            for index, theme in enumerate(variants)
            for at, corr in theme_matches(
                track,
                theme,
                hop_ms=settings.theme_hop_ms,
                threshold=settings.theme_threshold,
                track_spec=track_spec,
                theme_spec=theme_specs[index],
            )
        ]
        del track  # up to 270 MB for a 70-minute file
        peaks = _confirmed(ctx, source, series, _one_peak_per_place(found), end)
        peaks = _with_snapped_ends(
            peaks,
            track_spec=track_spec,
            theme_specs=theme_specs,
            zones=_zone_candidates(blacks, silences),
            origin_ms=start,
            end_ms=end,
            hop_ms=settings.theme_hop_ms,
            threshold=settings.theme_threshold,
            snap_ms=round(settings.theme_end_snap_s * 1000),
        )
    return Scan(blacks, silences, peaks)


def cartoon_removed(
    ctx: Context,
    source: Path,
    series_id: str,
    info: ProbeInfo,
    *,
    span: Span | None = None,
    scan: Scan | None = None,
    themes: ThemeLoader | None = None,
) -> Removed:
    """Zones to cut at the edges: black, silence, credits and title card, chained until stable.

    A zone counts when it touches an edge or a zone already removed from that edge, whatever the
    order in which they were found (a card, then black, then credits all go). Black and freeze
    are video facts: their end is compared with the video stream's, with one frame of slack.
    `span` cleans one child episode: the edges are those of that part of the file, and the zones
    come back in file time. `scan` is what a pass over the whole file already found.
    """
    series = ctx.config.series_by_id(series_id)
    total_ms = info.duration_ms
    start, end = span if span is not None else (0, total_ms)
    if span is not None:
        info = dataclasses.replace(
            info,
            duration_ms=end - start,
            video_duration_ms=min(max(info.video_duration_ms - start, 0), end - start),
        )
    duration, video_end = info.duration_ms, info.video_duration_ms
    frame_ms = math.ceil(1000 / info.fps) if info.fps > 0 else 34
    video_eps = EPS_MS + frame_ms
    tail_eps = video_eps + max(0, duration - video_end)  # the container outlasts the video

    if scan is None:
        scan = scan_source(
            ctx, source, series, info, silence_min_ms=MIN_BORDER_MS, span=span, themes=themes
        )
    blacks, silences = _to_local(scan.blacks, span), _to_local(scan.silences, span)
    # black frames are frozen too: only what is frozen and not black can be a title card
    frozen = detect_freeze(source, min_ms=MIN_FREEZE_MS, duration_ms=video_end, span=span)
    freezes = [piece for freeze in frozen for piece in subtract(freeze, blacks)]
    freezes = [(s, e) for s, e in freezes if e - s >= MIN_FREEZE_MS]
    refs = [ctx.paths.config_dir / r for r in series.intro_outro.reference_frames] if series else []
    matches = (
        _to_local(
            match_reference_frames(
                source, refs, tmp_dir=ctx.paths.tmp, duration_ms=total_ms, span=span
            ),
            span,
        )
        if refs
        else []
    )
    here = [p for p in scan.themes if start <= p.start < end]
    theme_spans = [(p.start - start, min(p.start - start + p.length_ms, duration)) for p in here]
    # the opening's end may differ from the reference: it was snapped to the zone that ends it
    opening_spans = [
        (p.start - start, min(max(p.opening_end - start, p.start - start + 1), duration))
        for p in here
    ]
    # a closing generic in the last part of the episode whose nominal end is near the end of the
    # episode (its length may differ from the reference) runs to the end
    reach = max(THEME_EDGE_MS, round(ctx.config.settings.clean.theme_end_snap_s * 1000))
    closing_spans = [
        (a, duration) for a, b in theme_spans if a > duration // 2 and b >= duration - reach
    ]
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
        _chain_front(removed, duration, opening_spans, "intro", eps_ms=THEME_EDGE_MS)
        _chain_back(removed, duration, theme_spans, "outro", eps_ms=THEME_EDGE_MS)
        _chain_back(removed, duration, closing_spans, "outro", eps_ms=THEME_EDGE_MS)
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
    return [(s + start, e + start, kind) for s, e, kind in removed]


@dataclass(frozen=True)
class _Row:
    """The columns of an episode that `clean` reads."""

    file_path: str
    series_id: str | None
    owner: str
    status: str
    parent: int | None
    span: Span | None


def _is_cartoon(ctx: Context, owner_account_id: str) -> bool:
    """Only a cartoon brand's videos are looked at for several episodes; unknown owner: no."""
    try:
        return ctx.config.brand_of(owner_account_id).vertical == "cartoons"
    except ConfigError:
        return False


class CleanStep(BaseStep):
    name: ClassVar[str] = "clean"
    heavy: ClassVar[bool] = True

    def __init__(self, *, preset: str = "veryfast") -> None:
        self.preset = preset
        self._themes = ThemeLoader()

    def key(self, item: int) -> str:
        return episode_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Imported episodes; with --force every matching one, analyzed ones answered by `run`.

        A `split` parent is never selected: its children are the episodes (they are cleaned in the
        same run that splits it, or by the next run when one of them failed).
        """
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
            span = (
                (episode.span_start_ms, episode.span_end_ms)
                if episode.span_start_ms is not None and episode.span_end_ms is not None
                else None
            )
            row = _Row(
                episode.file_path,
                episode.series_id,
                episode.owner_account_id,
                episode.status,
                episode.parent_episode_id,
                span,
            )
        return self._clean(ctx, item, row, force=force, inherited=None)

    def _clean(
        self, ctx: Context, item: int, row: _Row, *, force: bool, inherited: Scan | None
    ) -> Outcome:
        """Split the episode if it is a compilation, else clean it (`inherited`: parent's scan)."""
        if row.status == "split":  # its children are the episodes; re-splitting would orphan them
            return Outcome(
                "skipped",
                f"déjà découpé : {episode_key(item)} est nettoyé par ses épisodes enfants",
            )
        if row.status == "analyzed":  # redoing it would orphan its scenes, segments and posts
            return Outcome(
                "skipped", f"déjà analysé : {episode_key(item)} n'est pas nettoyé de nouveau"
            )
        source = ctx.paths.data_dir / row.file_path
        folder = ctx.paths.episode_dir(episode_key(item))
        clean_mp4, clean_json = folder / "clean.mp4", folder / "clean.json"

        if not force and self._finished(item, clean_mp4, clean_json):
            self._mark_cleaned(ctx, item, probe(clean_mp4).duration_ms, self._logo(ctx, clean_mp4))
            return Outcome("done", "fichiers déjà produits")

        info = probe(source)
        removed: Removed = []
        if row.series_id is not None:
            series = ctx.config.series_by_id(row.series_id)
            settings = ctx.config.settings.clean
            splittable = row.parent is None and _is_cartoon(ctx, row.owner)
            scan = inherited or scan_source(
                ctx,
                source,
                series,
                info,
                silence_min_ms=min(MIN_BORDER_MS, settings.boundary_min_ms)
                if splittable
                else MIN_BORDER_MS,
                black_min_ms=min(MIN_BLACK_SEEN_MS, settings.boundary_min_ms)
                if splittable
                else MIN_BLACK_SEEN_MS,
                span=row.span,
                themes=self._themes,
            )
            if splittable:
                spans = split_spans(
                    info.duration_ms,
                    theme_starts=[p.start for p in scan.themes],
                    boundaries=black_boundaries(
                        scan.blacks, scan.silences, min_ms=settings.boundary_min_ms
                    ),
                    s=settings,
                )
                if spans:
                    return self._split(ctx, item, spans, scan)
            removed = cartoon_removed(
                ctx, source, row.series_id, info, span=row.span, scan=scan, themes=self._themes
            )
        start, end = row.span if row.span is not None else (0, info.duration_ms)
        try:
            first, last = kept_range(end - start, [(s - start, e - start) for s, e, _ in removed])
        except ValueError as exc:
            raise MediaError(f"{episode_key(item)} : {exc}") from exc
        kept = (first + start, last + start)

        self._drop_analysis(folder)  # computed on the clean.mp4 about to be replaced
        self._encode(ctx, item, source, info, kept, clean_mp4)
        write_json(
            CleanFile(
                version=1,
                episode_id=item,
                source_duration_ms=info.duration_ms,
                kept=kept,
                removed=sorted(removed),
                span=row.span,
            ),
            clean_json,
        )
        self._mark_cleaned(ctx, item, probe(clean_mp4).duration_ms, self._logo(ctx, clean_mp4))
        gone = sum(e - s for s, e, _ in removed)
        return Outcome(
            "done", f"{(kept[1] - kept[0]) / 1000:.1f} s gardées, {gone / 1000:.1f} s retirées"
        )

    def _split(self, ctx: Context, item: int, spans: Sequence[Span], scan: Scan) -> Outcome:
        """Make the children (one transaction), then clean each of them in this same run."""
        children = self._create_children(ctx, item, spans)
        folder = ctx.paths.episode_dir(episode_key(item))
        for name in ("clean.mp4", "clean.json"):  # left by an earlier v1 clean of the whole file
            (folder / name).unlink(missing_ok=True)
        self._drop_analysis(folder)
        failed: list[str] = []
        for child, span in zip(children, spans, strict=True):
            key, began = episode_key(child), time.monotonic()
            try:
                with Session(ctx.engine) as session:
                    episode = session.get_one(Episode, child)
                    row = _Row(
                        episode.file_path,
                        episode.series_id,
                        episode.owner_account_id,
                        episode.status,
                        item,
                        span,
                    )
                outcome = self._clean(ctx, child, row, force=False, inherited=scan)
            except Exception as exc:  # the others go on; the next run picks this one up again
                failed.append(key)
                outcome = Outcome("failed", f"{type(exc).__name__}: {exc}".splitlines()[0])
            ms = int((time.monotonic() - began) * 1000)
            ctx.ui.emit(StepEvent(self.name, key, outcome.status, ms, outcome.message))
        keys = ", ".join(episode_key(c) for c in children)
        if failed:
            raise MediaError(
                f"{episode_key(item)} découpé en {len(children)} épisodes, "
                f"{', '.join(failed)} non nettoyé(s)",
                hint="Relancer `rushes clean` : les épisodes restants sont repris.",
            )
        return Outcome("done", f"découpé en {len(children)} épisodes : {keys}")

    @staticmethod
    def _create_children(ctx: Context, parent_id: int, spans: Sequence[Span]) -> list[int]:
        """One child per span, the parent `split`, in one transaction (the sha256 is the key)."""
        with Session(ctx.engine) as session, session.begin():
            parent = session.get_one(Episode, parent_id)
            ids: list[int] = []
            for k, (start, end) in enumerate(spans, 1):
                digest = f"{parent.sha256}:{k}"
                child = session.scalar(select(Episode).where(Episode.sha256 == digest))
                if child is None:
                    child = Episode(
                        series_id=parent.series_id,
                        creator_id=parent.creator_id,
                        source_video_id=parent.source_video_id,
                        provenance=parent.provenance,
                        owner_account_id=parent.owner_account_id,
                        file_path=parent.file_path,
                        sha256=digest,
                        duration_ms=end - start,
                        status="imported",
                        parent_episode_id=parent.id,
                        span_start_ms=start,
                        span_end_ms=end,
                    )
                    session.add(child)
                    session.flush()
                ids.append(child.id)
            parent.status = "split"
            parent.logo_corner = None  # a split parent has no picture of its own
            return ids

    @staticmethod
    def _logo(ctx: Context, clean_mp4: Path) -> str | None:
        s = ctx.config.settings.clean
        return detect_logo(
            clean_mp4,
            samples=s.logo_samples,
            static_std=s.logo_static_std,
            min_edge_ratio=s.logo_min_edge_ratio,
            static_share=s.logo_static_share,
            tmp_dir=ctx.paths.tmp,
        )

    @staticmethod
    def _drop_analysis(folder: Path) -> None:
        """Remove what `analyze` made from the old clean.mp4 (C43)."""
        (folder / "scenes.json").unlink(missing_ok=True)
        (folder / "audio.json").unlink(missing_ok=True)
        shutil.rmtree(folder / "keyframes", ignore_errors=True)

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
        args += [
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "2",
        ]
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
    def _mark_cleaned(ctx: Context, item: int, duration_ms: int, logo: str | None) -> None:
        with ctx.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE episodes SET status='cleaned', duration_ms=:d, logo_corner=:logo "
                    "WHERE id=:i AND status IN ('imported', 'cleaned')"
                ),
                {"d": duration_ms, "logo": logo, "i": item},
            )
