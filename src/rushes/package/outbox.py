"""`package` step (task 8b): a rendered post with caption and cover becomes a folder of `outbox/`.

`outbox/<account>/<p_key>/` holds video.mp4 (hard link of the render when on the same volume),
cover.jpg, caption.txt, hook.txt, hook_2.txt, hook_3.txt (those that exist) and upload.json; a post
of a series without licence or of an unauthorized creator goes to `outbox_test/` (R14, R22, R32).
Nothing is ever sent to a platform (R48): Louis publishes by hand.

Files first (each through `staged`), then one short `BEGIN IMMEDIATE` transaction: the slot is
checked again, a guarded UPDATE moves the post to `ready`, the renames follow, the commit last
(C46). A change made during the work matches no row: the staged files are dropped (`skipped`).
"""

import contextlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.assign.assign import is_test_post
from rushes.context import Context, Filters
from rushes.core.timecode import parse_instant
from rushes.db.engine import write_session
from rushes.db.models import Episode, Post, Segment
from rushes.errors import RushesError
from rushes.fsutil import Staged, staged
from rushes.ids import post_key
from rushes.media.verify import check_delivery
from rushes.package.index_html import refresh_index
from rushes.package.upload import COVER_NAME, VIDEO_NAME, build_upload, earlier_parts
from rushes.queue.slots import plan_slots
from rushes.runner import BaseStep, Outcome
from rushes.schemas import UploadFile

UPLOAD_NAME = "upload.json"
HOOK_NAMES = ("hook.txt", "hook_2.txt", "hook_3.txt")
MAX_SLOT_PROBE = 1024  # free slots looked at to find one after the previous part


def _next_slot(ctx: Context, session: Session, post: Post, *, after: datetime | None) -> datetime:
    """The first free slot of the account, after `after` (the slot of the part before)."""
    account = ctx.config.account(post.account_id)
    tz = ZoneInfo(ctx.config.brand(account.brand).timezone)
    ratio = ctx.config.accounts.posts_per_day.get(account.platform, 0)
    taken = [
        parse_instant(value)
        for value in session.scalars(
            select(Post.planned_at).where(
                Post.account_id == post.account_id,
                Post.id != post.id,
                Post.status != "discarded",
                Post.planned_at.is_not(None),
            )
        )
        if value is not None
    ]
    size = 4
    while True:
        found = plan_slots(
            slots=ctx.config.slots_for(account.id),
            posts_per_day=ratio,
            tz=tz,
            start=ctx.clock.now(),
            taken=taken,
            n=size,
        )
        usable = [moment for moment in found if after is None or moment > after]
        if usable:
            return usable[0]
        if len(found) < size or size >= MAX_SLOT_PROBE:
            raise RushesError(
                f"{post_key(post.id)} : aucun créneau libre pour {account.id} "
                f"(posts_per_day = {ratio})"
            )
        size *= 2


def _slot(ctx: Context, session: Session, post: Post, segment: Segment) -> datetime | str:
    """The slot of the post: the one it holds, else the next free one after every earlier part.

    The earlier parts are the visible ones of the same episode and account (not discarded,
    expired or rejected). A string is the reason why the post must wait: the nearest earlier part
    has no slot yet."""
    if post.planned_at is not None:
        return parse_instant(post.planned_at)
    after: datetime | None = None
    if segment.kind == "part":
        _, earlier = earlier_parts(session, post, segment)
        if earlier:
            nearest, number = earlier[-1]
            if nearest.planned_at is None and nearest.status in ("planned", "rendered"):
                return f"attend la partie {number} ({post_key(nearest.id)})"
        held = [
            parse_instant(before.planned_at)
            for before, _ in earlier
            if before.planned_at is not None
        ]
        after = max(held, default=None)
    return _next_slot(ctx, session, post, after=after)


def _revoked(ctx: Context, session: Session, post: Post, segment: Segment) -> str | None:
    """Why a post not marked as a test must not go to outbox/ (R14, R22), else None.

    `is_test` is fixed by `assign`; a licence or an authorization taken back since then makes it
    stale. The opposite (a test post whose licence came) stays in outbox_test/: re-assigning it is
    a matter for the queue (16b) and the licence sync (40a)."""
    if post.is_test or not is_test_post(ctx.config, session, segment.episode_id):
        return None
    episode = session.get_one(Episode, segment.episode_id)
    cause = (
        "série sans licence (R14)"
        if episode.series_id is not None
        else "créateur non autorisé (R22)"
    )
    return (
        f"{post_key(post.id)} : {cause} alors que le post n'est pas marqué test : "
        "pas de paquet dans outbox/ ; écarter le post, le réattribuer en vitrine ou rétablir "
        "`licensed`/`authorized` dans la config"
    )


def _place(source: Path, target: Path) -> None:
    """Hard link when the volume allows it, else a copy; `target` is a free staged temporary."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


class _Seen:
    """What `run` read: the final UPDATE only applies to this state of the row."""

    def __init__(self, post: Post) -> None:
        self.caption = post.caption
        self.version = post.render_version
        self.part = post.part_number
        self.test = bool(post.is_test)
        self.planned = post.planned_at
        self.account = post.account_id


class PackageStep(BaseStep):
    name: ClassVar[str] = "package"

    def key(self, item: int) -> str:
        return post_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Rendered posts with a caption, a finished render, an approved segment (R3) and their
        meta files; parts in the order of their number within an episode.

        --force adds nothing: every such post is taken already, and a `ready` one never is."""
        query = (
            select(Post.id)
            .join(Segment, Segment.id == Post.segment_id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(
                Post.status == "rendered",
                Post.caption.is_not(None),
                Post.render_version.is_not(None),
                Segment.status == "approved",
            )
            .order_by(Segment.episode_id, Post.part_number.is_(None), Post.part_number, Post.id)
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
            chosen = list(session.scalars(query))
        return [item for item in chosen if self._has_meta(ctx, item)]

    @staticmethod
    def _has_meta(ctx: Context, item: int) -> bool:
        key = post_key(item)
        return (ctx.paths.renders / f"{key}.meta.json").is_file() and (
            ctx.paths.renders / f"{key}.cover.jpg"
        ).is_file()

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        key = post_key(item)
        with Session(ctx.engine) as session:  # read, close, then the slow work
            post = session.get(Post, item)
            if post is None:
                return Outcome("skipped", "post introuvable")
            segment = session.get(Segment, post.segment_id) if post.segment_id else None
            if segment is None:
                return Outcome("skipped", f"{key} n'est pas un passage : paquet non géré ici")
            if post.status == "ready":
                return Outcome("skipped", f"déjà prêt : {key}")
            if post.status != "rendered":
                return Outcome("skipped", f"{key} est {post.status} : pas de paquet")
            if segment.status != "approved":  # R3
                return Outcome("skipped", f"passage non approuvé : {key} n'est pas empaqueté")
            if post.caption is None or post.render_version is None:
                return Outcome("skipped", f"{key} attend sa légende et son rendu")
            problem = _revoked(ctx, session, post, segment)
            if problem is not None:
                return Outcome("failed", problem)
            seen = _Seen(post)
            slot = _slot(ctx, session, post, segment)
            if isinstance(slot, str):
                return Outcome("skipped", f"{key} {slot} : créneaux dans l'ordre des parties")
            upload = build_upload(ctx.config, ctx.paths, session, item, planned_at=slot)
        return self._pack(ctx, item, seen, slot, upload)

    def _pack(
        self, ctx: Context, item: int, seen: _Seen, slot: datetime, upload: UploadFile
    ) -> Outcome:
        key = post_key(item)
        video = ctx.paths.renders / f"{key}.mp4"
        cover = ctx.paths.renders / f"{key}.cover.jpg"
        if not video.is_file():
            return Outcome(
                "failed", f"{key} : vidéo rendue introuvable ; `rushes render --force` la refait"
            )
        problems = check_delivery(video)
        if problems:
            return Outcome("failed", f"{key} : vidéo non conforme à R7 ({' ; '.join(problems)})")
        folder = ctx.paths.post_dir(seen.account, key, test=seen.test)
        texts = {"caption.txt": upload.caption}
        if upload.hook.applied is not None:
            texts["hook.txt"] = upload.hook.applied
        for rank, alternative in enumerate(upload.hook.alternatives[:2], start=2):
            texts[f"hook_{rank}.txt"] = alternative
        names = (VIDEO_NAME, COVER_NAME, *texts, UPLOAD_NAME)
        refusal: Outcome | None = None
        packed = False
        try:
            with staged(*(folder / name for name in names)) as stage:
                _place(video, stage.path(folder / VIDEO_NAME))
                _place(cover, stage.path(folder / COVER_NAME))
                for name, content in texts.items():
                    stage.path(folder / name).write_text(content, encoding="utf-8")
                stage.path(folder / UPLOAD_NAME).write_text(
                    upload.model_dump_json(indent=1) + "\n", encoding="utf-8"
                )
                stale = [folder / name for name in HOOK_NAMES if name not in texts]
                refusal = self._commit(ctx, item, seen, slot, stage, stale)
                packed = refusal is None
        finally:
            if not packed:  # refused or failed: nothing was packed, leave no empty folder
                for empty in (folder, folder.parent, folder.parent.parent):
                    with contextlib.suppress(OSError):
                        empty.rmdir()  # only when it is empty
        if refusal is not None:
            return refusal
        return Outcome(
            "done", f"{ctx.paths.relative(folder)}, créneau {upload.publish_at.brand_local}"
        )

    @staticmethod
    def _commit(
        ctx: Context, item: int, seen: _Seen, slot: datetime, stage: Staged, stale: list[Path]
    ) -> Outcome | None:
        """Short transaction: R14/R22 and the slot checked again, compare-and-swap UPDATE, renames,
        commit. None when the post is packed, else the outcome (nothing renamed).

        The licence is read again inside the lock, so a revocation that lands during the work is
        not a gap. `skipped` when the row is no longer as `run` read it: another status, caption,
        render, number, test flag or slot, or a segment that is no longer approved (R3)."""
        changed = Outcome(
            "skipped", f"{post_key(item)} a changé pendant le travail : fichiers jetés"
        )
        with write_session(ctx.engine) as session:  # takes the write lock first
            post = session.get(Post, item)
            segment = session.get(Segment, post.segment_id) if post and post.segment_id else None
            if post is None or segment is None:
                return changed
            problem = _revoked(ctx, session, post, segment)
            if problem is not None:
                return Outcome("failed", problem)
            if seen.planned is None and _slot(ctx, session, post, segment) != slot:
                return changed  # another process took the slot meanwhile
            done = session.connection().execute(
                text(
                    "UPDATE posts SET status='ready', planned_at=:p WHERE id=:i "
                    "AND status='rendered' AND caption IS :caption AND render_version IS :version "
                    "AND part_number IS :part AND is_test IS :test AND planned_at IS :seen "
                    "AND EXISTS (SELECT 1 FROM segments s WHERE s.id=posts.segment_id "
                    "AND s.status='approved')"
                ),
                {
                    "p": seen.planned or slot.astimezone(UTC).isoformat(timespec="seconds"),
                    "i": item,
                    "caption": seen.caption,
                    "version": seen.version,
                    "part": seen.part,
                    "test": int(seen.test),
                    "seen": seen.planned,
                },
            )
            if done.rowcount != 1:
                return changed
            stage.commit()
            for path in stale:  # a hook file of an earlier attempt that no longer applies
                path.unlink(missing_ok=True)
            session.commit()
        return None

    def finish(self, ctx: Context) -> None:
        """INDEX.html of outbox/ and outbox_test/, rewritten only when its content changes."""
        for test in (False, True):
            refresh_index(ctx, test=test)
