"""`render` step (task 7a): a segment post becomes a 9:16 plate, then its final video.

The plate is the picture on the canvas with two-pass loudnorm sound (R7); the final video is, in
7a, a copy of it (task 7b adds the overlays). Source: `clean.mp4` of the episode, the segment's
times being in its time. Files first (each atomic), then one short transaction (C32).
"""

import shutil
from typing import ClassVar, cast, get_args

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from rushes.config import Layout
from rushes.context import Context, Filters
from rushes.db.models import Episode, Post, Segment
from rushes.errors import MediaError, RushesError
from rushes.fsutil import Staged, staged
from rushes.ids import episode_key, post_key
from rushes.media.verify import check_delivery
from rushes.render.encode import render_plate
from rushes.render.loudnorm import TARGET_LUFS, TOLERANCE_LU
from rushes.runner import BaseStep, Outcome
from rushes.ui import Progress, format_duration

RENDER_PLATE_VERSION = "plate-1"  # bump when the plate's look changes: old 'rendered' posts redo
DEFAULT_LAYOUT: Layout = "square"  # R8, when the config lists no preference

# `ready` is selected only with --force, to say why it is left alone.
_FORCEABLE = ("planned", "rendered", "ready")


class RenderStep(BaseStep):
    name: ClassVar[str] = "render"
    heavy: ClassVar[bool] = True

    def key(self, item: int) -> str:
        return post_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Planned posts with an approved segment (R3); rendered posts of an older plate version.

        A `ready` post is never selected by a version bump (Louis may have scheduled it)."""
        todo = or_(
            Post.status == "planned",
            and_(
                Post.status == "rendered",
                or_(Post.render_version.is_(None), Post.render_version != RENDER_PLATE_VERSION),
            ),
        )
        if filters.force:
            todo = Post.status.in_(_FORCEABLE)
        query = (
            select(Post.id)
            .join(Segment, Segment.id == Post.segment_id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(todo, Segment.status == "approved")
            .order_by(Post.id)
        )
        if filters.posts:
            query = query.where(Post.id.in_(filters.posts))
        if filters.account is not None:
            query = query.where(Post.account_id == filters.account)
        if filters.episode is not None:
            query = query.where(Episode.id == filters.episode)
        if filters.episodes:
            query = query.where(Episode.id.in_(filters.episodes))
        if filters.series is not None:
            query = query.where(Episode.series_id == filters.series)
        if filters.creator is not None:
            query = query.where(Episode.creator_id == filters.creator)
        with Session(ctx.engine) as session:
            return list(session.scalars(query))

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = post_key(item)
        with Session(ctx.engine) as session:  # read, close, then the slow work
            post = session.get(Post, item)
            if post is None:
                return Outcome("skipped", "post introuvable")
            segment = session.get(Segment, post.segment_id) if post.segment_id else None
            status, version, layout = post.status, post.render_version, post.layout
            if segment is None:
                return Outcome("skipped", f"{key} n'est pas un passage : rendu non géré ici")
            span, segment_status, episode_id = (
                (segment.start_ms, segment.end_ms),
                segment.status,
                segment.episode_id,
            )
        if status == "ready":
            return Outcome(
                "skipped",
                f"déjà prêt : {key} n'est pas rendu de nouveau "
                "(seul un changement d'accroche, de numéro ou de licence le remet en rendu)",
            )
        if status not in ("planned", "rendered"):
            return Outcome("skipped", f"{key} est {status} : rien à rendre")
        if segment_status != "approved":  # R3
            return Outcome("skipped", f"passage non approuvé : {key} n'est pas rendu")
        if status == "rendered" and version == RENDER_PLATE_VERSION and not force:
            return Outcome("skipped", f"déjà rendu : {key}")

        source = ctx.paths.episode_dir(episode_key(episode_id)) / "clean.mp4"
        if not source.is_file():
            raise MediaError(
                f"{key} : {episode_key(episode_id)}/clean.mp4 introuvable",
                hint="Relancer `rushes clean` pour cet épisode.",
            )
        name = layout or self._default_layout(ctx)
        if name not in get_args(Layout):
            raise RushesError(f"{key} : mise en page inconnue ({name})")
        chosen = cast(Layout, name)
        plate = ctx.paths.renders / f"{key}.plate.mp4"
        final = ctx.paths.renders / f"{key}.mp4"
        task = f"render {key}"

        def progress(percent: int) -> None:
            ctx.ui.emit(Progress(task, percent, 100, unit="%"))  # the console throttles it

        with staged(plate, final) as stage:  # nothing replaces an older file before the guard
            plate_tmp, final_tmp = stage.path(plate), stage.path(final)
            reached = render_plate(
                source, span, chosen, plate_tmp, s=ctx.config.settings.render, on_progress=progress
            )
            on_target = reached is None or abs(reached.output_i - TARGET_LUFS) <= TOLERANCE_LU
            if not on_target:  # written so that nan fails too
                assert reached is not None
                raise MediaError(
                    f"{key} : loudness {reached.output_i:.1f} LUFS hors de "
                    f"{TARGET_LUFS:g} ± {TOLERANCE_LU:g} LU (R7), mode {reached.normalization_type}"
                )
            problems = check_delivery(plate_tmp)
            if problems:
                raise MediaError(f"{key} : rendu non conforme à R7 ({' ; '.join(problems)})")
            shutil.copyfile(plate_tmp, final_tmp)  # task 7b: overlays pass instead of a copy
            if not self._commit(ctx, item, ctx.paths.relative(final), stage, force=force):
                return Outcome("skipped", f"{key} n'est plus à rendre : fichiers jetés")
        note = (
            " (loudnorm dynamique)" if reached and reached.normalization_type == "dynamic" else ""
        )
        return Outcome("done", f"{chosen}, {format_duration(span[1] - span[0])}{note}")

    @staticmethod
    def _default_layout(ctx: Context) -> str:
        preference = ctx.config.accounts.layouts.preference
        return preference[0] if preference else DEFAULT_LAYOUT

    @staticmethod
    def _commit(ctx: Context, item: int, video_path: str, stage: Staged, *, force: bool) -> bool:
        """Short transaction (C32): guarded UPDATE first, then the O(1) renames, then commit.

        False (nothing changed, the caller drops the temporaries) when the post is no longer to
        render: another writer discarded it, made it ready or withdrew its segment meanwhile. A
        crash after the renames leaves new files and the old row: the next run redoes them."""
        stale = "" if force else " AND (render_version IS NULL OR render_version <> :v)"
        with ctx.engine.begin() as conn:
            done = conn.execute(
                text(
                    "UPDATE posts SET status='rendered', render_version=:v, video_path=:p "
                    "WHERE id=:i AND (status='planned' OR (status='rendered'" + stale + ")) "
                    "AND EXISTS (SELECT 1 FROM segments s WHERE s.id=posts.segment_id "
                    "AND s.status='approved')"
                ),
                {"v": RENDER_PLATE_VERSION, "p": video_path, "i": item},
            )
            if done.rowcount != 1:
                return False
            stage.commit()
        return True
