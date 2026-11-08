"""`render` step (tasks 7a, 7b): a segment post becomes a 9:16 plate, then its final video.

The plate is the picture on the canvas with two-pass loudnorm sound (R7); the final video is the
plate plus the overlays of the post's originality module (banner, hook, bar, end card). Source:
`clean.mp4` of the episode, the segment's times being in its time. Files first (each atomic), then
one short transaction (C32).

Light re-render: a plate that carries the tag of the current plate version, layout and span is kept
and only the overlays pass is redone (a new hook or number). The tag lives in the plate's own
metadata, so it cannot disagree with the file. `render_version` moves with the final only; a plate
newer than its final (a rename that failed in between) is repaired by the next run (C46).
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast, get_args

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from rushes.config import Layout, Originality
from rushes.context import Context, Filters
from rushes.db.models import Episode, Post, Segment
from rushes.errors import MediaError, RushesError
from rushes.fsutil import Staged, staged
from rushes.ids import episode_key, post_key
from rushes.media.probe import probe
from rushes.media.verify import check_delivery
from rushes.originality import module_for
from rushes.originality.base import OriginalityModule
from rushes.render.canvas import CANVAS_H, CANVAS_W, Rect, video_rect
from rushes.render.encode import DURATION_TOLERANCE_MS, read_plate_tag, render_plate
from rushes.render.job import build_job
from rushes.render.loudnorm import TARGET_LUFS, TOLERANCE_LU
from rushes.render.overlays import BAR_PX, RENDER_OVERLAY_VERSION, render_overlays
from rushes.runner import BaseStep, Outcome
from rushes.ui import Message, Progress, format_duration

RENDER_PLATE_VERSION = "plate-1"  # bump when the plate's look changes: old 'rendered' posts redo
# a row at this version has its plate AND its overlays up to date
RENDER_VERSION = f"{RENDER_PLATE_VERSION}+{RENDER_OVERLAY_VERSION}"
DEFAULT_LAYOUT: Layout = "square"  # R8, when the config lists no preference

# `ready` is selected only with --force, to say why it is left alone.
_FORCEABLE = ("planned", "rendered", "ready")


def plate_tag(layout: Layout, span: tuple[int, int], source: Path, episode: int) -> str:
    """What a plate is made from, written in its metadata: a plate with another tag is redone.

    The source is the episode and the size and mtime of its `clean.mp4`, so that a re-cleaned
    episode never reuses a plate of other pixels. Task 34 adds the crop window when the reframing
    exists."""
    seen = source.stat()
    key = f"{episode_key(episode)} {seen.st_size} {seen.st_mtime_ns}"
    return f"rushes-plate {RENDER_PLATE_VERSION} {layout} {span[0]}-{span[1]} {key}"


@dataclass(frozen=True)
class _Seen:
    """The post as `run` read it: the final UPDATE only applies to this state."""

    status: str
    version: str | None
    hook_id: int | None
    layout: str | None


@dataclass(frozen=True)
class _Job:
    key: str
    item: int
    source: Path
    span: tuple[int, int]
    layout: Layout
    plate: Path
    final: Path
    tag: str
    module: OriginalityModule
    seen: _Seen


def _pair_stale(plate: Path, final: Path) -> bool:
    """True when the final is missing or older than its plate (the files do not belong together).

    The final is always written after the plate it is built from, so in a consistent pair its
    mtime is the later one; a plate renamed without its final (the second rename failed) breaks
    that."""
    if not plate.is_file() or not final.is_file():
        return True
    return final.stat().st_mtime_ns < plate.stat().st_mtime_ns


class RenderStep(BaseStep):
    name: ClassVar[str] = "render"
    heavy: ClassVar[bool] = True

    def key(self, item: int) -> str:
        return post_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Planned posts with an approved segment (R3); rendered posts of an older version, or
        whose plate and final no longer belong together.

        A `ready` post is never selected by a version bump (Louis may have scheduled it)."""
        todo = or_(
            Post.status == "planned",
            and_(
                Post.status == "rendered",
                or_(Post.render_version.is_(None), Post.render_version != RENDER_VERSION),
            ),
        )
        if filters.force:
            todo = Post.status.in_(_FORCEABLE)
        base = (
            select(Post.id)
            .join(Segment, Segment.id == Post.segment_id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(Segment.status == "approved")
            .order_by(Post.id)
        )
        if filters.posts:
            base = base.where(Post.id.in_(filters.posts))
        if filters.account is not None:
            base = base.where(Post.account_id == filters.account)
        if filters.episode is not None:
            base = base.where(Episode.id == filters.episode)
        if filters.episodes:
            base = base.where(Episode.id.in_(filters.episodes))
        if filters.series is not None:
            base = base.where(Episode.series_id == filters.series)
        if filters.creator is not None:
            base = base.where(Episode.creator_id == filters.creator)
        with Session(ctx.engine) as session:
            chosen = set(session.scalars(base.where(todo)))
            if not filters.force:  # up-to-date rows whose files are not a pair
                current = session.scalars(
                    base.where(Post.status == "rendered", Post.render_version == RENDER_VERSION)
                )
                for post_id in current:
                    plate = ctx.paths.renders / f"{post_key(post_id)}.plate.mp4"
                    if _pair_stale(plate, ctx.paths.renders / f"{post_key(post_id)}.mp4"):
                        chosen.add(post_id)
        return sorted(chosen)

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = post_key(item)
        with Session(ctx.engine) as session:  # read, close, then the slow work
            post = session.get(Post, item)
            if post is None:
                return Outcome("skipped", "post introuvable")
            segment = session.get(Segment, post.segment_id) if post.segment_id else None
            seen = _Seen(post.status, post.render_version, post.hook_id, post.layout)
            originality = post.originality or ctx.config.effective_originality(post.account_id)
            if segment is None:
                return Outcome("skipped", f"{key} n'est pas un passage : rendu non géré ici")
            span, segment_status, episode_id = (
                (segment.start_ms, segment.end_ms),
                segment.status,
                segment.episode_id,
            )
        if seen.status == "ready":
            return Outcome(
                "skipped",
                f"déjà prêt : {key} n'est pas rendu de nouveau "
                "(seul un changement d'accroche, de numéro ou de licence le remet en rendu)",
            )
        if seen.status not in ("planned", "rendered"):
            return Outcome("skipped", f"{key} est {seen.status} : rien à rendre")
        if segment_status != "approved":  # R3
            return Outcome("skipped", f"passage non approuvé : {key} n'est pas rendu")
        plate = ctx.paths.renders / f"{key}.plate.mp4"
        final = ctx.paths.renders / f"{key}.mp4"
        up_to_date = seen.status == "rendered" and seen.version == RENDER_VERSION
        if up_to_date and not force and not _pair_stale(plate, final):
            return Outcome("skipped", f"déjà rendu : {key}")

        source = ctx.paths.episode_dir(episode_key(episode_id)) / "clean.mp4"
        if not source.is_file():
            raise MediaError(
                f"{key} : {episode_key(episode_id)}/clean.mp4 introuvable",
                hint="Relancer `rushes clean` pour cet épisode.",
            )
        name = seen.layout or self._default_layout(ctx)
        if name not in get_args(Layout):
            raise RushesError(f"{key} : mise en page inconnue ({name})")
        chosen = cast(Layout, name)
        try:
            module = module_for(cast(Originality, originality))
        except NotImplementedError as exc:
            raise RushesError(
                f"{key} : module d'originalité {originality} pas encore disponible ({exc}) ; "
                "mettre `originality: base` pour ce compte dans accounts.yaml en attendant",
                hint="Mettre `originality: base` pour ce compte dans accounts.yaml en attendant.",
            ) from exc
        tag = plate_tag(chosen, span, source, episode_id)
        job = _Job(key, item, source, span, chosen, plate, final, tag, module, seen)
        light = not force and read_plate_tag(plate) == tag  # the plate is reused as it is
        if not light:
            return self._make(ctx, job, light=False)
        try:
            return self._make(ctx, job, light=True)
        except MediaError as exc:  # a plate cut short after its header carries the right tag
            first = str(exc).splitlines()[0]
            ctx.ui.emit(
                Message("warn", f"{key} : plaque gardée inutilisable ({first}), rendue de nouveau")
            )
            return self._make(ctx, job, light=False)

    def _make(self, ctx: Context, job: _Job, *, light: bool) -> Outcome:
        """Overlays on the plate that is there (`light`) or on a freshly rendered one."""
        key, s = job.key, ctx.config.settings.render
        duration = job.span[1] - job.span[0]
        info = probe(job.source)
        task = f"render {key}"

        def progress(percent: int) -> None:
            ctx.ui.emit(Progress(task, percent, 100, unit="%"))  # the console throttles it

        ctx.paths.tmp.mkdir(parents=True, exist_ok=True)
        files = (job.final,) if light else (job.plate, job.final)
        with staged(*files) as stage, tempfile.TemporaryDirectory(dir=ctx.paths.tmp) as scratch:
            # the job and the plan first: a bad charter or avatar costs no plate (the span is the
            # duration, the plate is within 150 ms of it)
            with Session(ctx.engine) as session:
                made = build_job(
                    ctx,
                    session,
                    job.item,
                    layout=job.layout,
                    duration_ms=duration,
                    src_w=info.display_width,
                    src_h=info.height,
                )
            plan = job.module.plan(made, Path(scratch))
            reached = None
            if light:
                plate_src = job.plate
            else:
                plate_src = stage.path(job.plate)  # nothing replaces an older file before the guard
                reached = render_plate(
                    job.source,
                    job.span,
                    job.layout,
                    plate_src,
                    s=s,
                    on_progress=progress,
                    tag=job.tag,
                )
                on_target = reached is None or abs(reached.output_i - TARGET_LUFS) <= TOLERANCE_LU
                if not on_target:  # written so that nan fails too
                    assert reached is not None
                    raise MediaError(
                        f"{key} : loudness {reached.output_i:.1f} LUFS hors de "
                        f"{TARGET_LUFS:g} ± {TOLERANCE_LU:g} LU (R7), "
                        f"mode {reached.normalization_type}"
                    )
                problems = check_delivery(plate_src)
                if problems:
                    raise MediaError(f"{key} : rendu non conforme à R7 ({' ; '.join(problems)})")
            plate_ms = duration  # the plate lasts the span, within 150 ms (render_plate checks)
            final_tmp = stage.path(job.final)
            rect = video_rect(job.layout, made.src_w, made.src_h)
            bar = Rect(0, min(rect.y + rect.h, CANVAS_H - BAR_PX), CANVAS_W, BAR_PX)
            render_overlays(
                plate_src,
                plan,
                final_tmp,
                duration_ms=plate_ms,
                bar_rect=bar,
                bar_color=made.charter.progress,
                s=s,
                on_progress=progress if light else None,
            )
            problems = check_delivery(final_tmp)
            made_ms = probe(final_tmp).duration_ms
            if abs(made_ms - plate_ms) > DURATION_TOLERANCE_MS:
                problems.append(f"durée {made_ms} ms au lieu de {plate_ms} ms")
            if problems:
                raise MediaError(
                    f"{key} : habillage non conforme à R7 ({' ; '.join(problems)}) ; "
                    "`rushes render --force` refait aussi la plaque",
                    hint="`rushes render --force` refait aussi la plaque.",
                )
            if not self._commit(
                ctx, job, ctx.paths.relative(job.final), stage, part_number=made.part_number
            ):
                return Outcome("skipped", f"{key} n'est plus à rendre : fichiers jetés")
        note = (
            " (loudnorm dynamique)" if reached and reached.normalization_type == "dynamic" else ""
        )
        note += " (incrustations seules)" if light else ""
        return Outcome("done", f"{job.layout}, {format_duration(duration)}{note}")

    @staticmethod
    def _default_layout(ctx: Context) -> str:
        preference = ctx.config.accounts.layouts.preference
        return preference[0] if preference else DEFAULT_LAYOUT

    @staticmethod
    def _commit(
        ctx: Context, job: _Job, video_path: str, stage: Staged, *, part_number: int | None
    ) -> bool:
        """Short transaction (C32): compare-and-swap UPDATE, then the O(1) renames, then commit.

        The UPDATE only applies to the row as `run` read it (status, version, hook, layout)
        and to a segment still approved: a hook or number reset, a discard or a `ready` that landed
        during the render makes it match nothing (False: the caller drops the temporaries and the
        next run picks the post up again). A crash after the renames leaves new files and the old
        row: the next run redoes them."""
        seen = job.seen
        with ctx.engine.begin() as conn:
            done = conn.execute(
                text(
                    "UPDATE posts SET status='rendered', render_version=:v, video_path=:p, "
                    "part_number=:n WHERE id=:i AND status=:status "
                    "AND render_version IS :seen AND hook_id IS :hook AND layout IS :layout "
                    "AND EXISTS (SELECT 1 FROM segments s WHERE s.id=posts.segment_id "
                    "AND s.status='approved')"
                ),
                {
                    "v": RENDER_VERSION,
                    "p": video_path,
                    "n": part_number,
                    "i": job.item,
                    "status": seen.status,
                    "seen": seen.version,
                    "hook": seen.hook_id,
                    "layout": seen.layout,
                },
            )
            if done.rowcount != 1:
                return False
            stage.commit()
        return True
