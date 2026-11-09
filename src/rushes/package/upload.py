"""`upload.json` of a post (task 8b): everything Louis types in the app, in the order of the screen.

No `Context`, no heavy import: `rushes serve` (task 36) builds it too. The suggested time is
`posts.planned_at` (UTC), or the value the caller is about to write (`planned_at`): `package` builds
the file before the row is updated.
"""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.core.timecode import parse_instant
from rushes.core.types import Platform
from rushes.db.models import Episode, Post, Segment
from rushes.errors import RushesError
from rushes.ids import post_key
from rushes.paths import Paths
from rushes.queue.numbering import GONE
from rushes.schemas import (
    MetaFile,
    PublishAt,
    UploadFile,
    UploadFiles,
    UploadHook,
    read_json,
)

if TYPE_CHECKING:  # only a type here: rushes.config (yaml, ui, rich) stays unloaded
    from rushes.config import Config

PARIS = ZoneInfo("Europe/Paris")
VIDEO_NAME, COVER_NAME = "video.mp4", "cover.jpg"
TEST_SERIES = "NE PAS PUBLIER : série sans licence (vitrine)"
TEST_CREATOR = "NE PAS PUBLIER : créateur non autorisé"
AI_REMINDER = "Activer l'étiquette « contenu généré par IA » si l'appli la demande"


def read_meta(paths: Paths, post: Post) -> MetaFile:
    """`renders/<p_key>.meta.json`, which must agree with the caption and hashtags of the row."""
    key = post_key(post.id)
    try:
        meta = read_json(MetaFile, paths.renders / f"{key}.meta.json")
    except (OSError, ValueError) as exc:
        raise RushesError(
            f"{key} : meta.json absent ou illisible ({exc})",
            hint="Relancer `rushes meta --force` pour ce post.",
        ) from exc
    tags = json.loads(post.hashtags) if post.hashtags is not None else None
    if meta.post_id != post.id or meta.caption != post.caption or tags != meta.hashtags:
        raise RushesError(
            f"{key} : meta.json ne correspond pas à la légende de la base",
            hint="Relancer `rushes meta --force` pour ce post.",
        )
    return meta


def _moment(post: Post, planned_at: datetime | None) -> datetime:
    if planned_at is not None:
        return planned_at.astimezone(UTC)
    if post.planned_at is None:
        raise RushesError(f"{post_key(post.id)} : pas de créneau conseillé (planned_at vide)")
    return parse_instant(post.planned_at)


def earlier_parts(
    session: Session, post: Post, segment: Segment
) -> tuple[int, list[tuple[Post, int]]]:
    """(number of the post, the parts before it) among the visible parts of the account.

    Visible: same episode and account, post not discarded or expired, segment not rejected (as
    `is_last_part` reads it). Earlier means earlier in the episode (`start_ms`), the nearest last.
    A part's number is its fixed `part_number`, else its rank."""
    rows = session.execute(
        select(Post, Segment.start_ms, Segment.status)
        .join(Segment, Segment.id == Post.segment_id)
        .where(
            Segment.kind == "part",
            Segment.episode_id == segment.episode_id,
            Post.account_id == post.account_id,
            Post.status.not_in(GONE),
        )
        .order_by(Segment.start_ms, Post.id)
    ).all()
    numbered = {
        row.Post.id: (row.Post.part_number if row.Post.part_number is not None else rank)
        for rank, row in enumerate(rows, start=1)
    }
    own = numbered.get(post.id, post.part_number or 1)
    earlier = [
        (row.Post, numbered[row.Post.id])
        for row in rows
        if (row[1], row.Post.id) < (segment.start_ms, post.id) and row[2] != "rejected"
    ]
    return own, earlier


def _voice_is_current(paths: Paths, post: Post) -> bool:
    """A voice file (task 35) counts only for the render it was made for."""
    if not (paths.renders / f"{post_key(post.id)}.voice.json").is_file():
        return False
    from rushes.render.render import RENDER_VERSION  # late: only when a voice file exists

    return post.render_version == RENDER_VERSION


def _settings(config: Config, platform: Platform, *, voice: bool) -> dict[str, Any]:
    settings: dict[str, Any] = dict(config.accounts.upload_defaults.get(platform, {}))
    if platform == "tiktok":
        settings["ai_generated_label"] = voice
        settings["branded_content"] = False
    elif voice:
        settings["ai_generated_label"] = True
    return settings


def _checklist(
    platform: Platform, cover_frame_s: float, *, test: str | None, voice: bool
) -> list[str]:
    if platform == "tiktok":
        at = f"{cover_frame_s:.1f}".replace(".", ",")
        items = [
            f"Couverture : image à {at} s ou cover.jpg",
            "Vérifier que la légende tient avant « plus »",
        ]
    else:
        items = ["Couverture : cover.jpg", "Partager dans le fil activé"]
    if voice:
        items.append(AI_REMINDER)
    return [test, *items] if test else items


def build_upload(
    config: Config,
    paths: Paths,
    session: Session,
    post_id: int,
    *,
    planned_at: datetime | None = None,
) -> UploadFile:
    """The `upload.json` of a rendered post whose meta files exist.

    `planned_at`: the slot to show when the row does not hold it yet. RushesError (French) when
    the post, its segment, its caption or its meta file is missing or disagrees."""
    key = post_key(post_id)
    post = session.get(Post, post_id)
    if post is None:
        raise RushesError(f"{key} : post introuvable")
    segment = session.get(Segment, post.segment_id) if post.segment_id is not None else None
    if segment is None:
        raise RushesError(f"{key} : pas un passage, upload.json non géré ici")
    if post.caption is None:
        raise RushesError(f"{key} : pas de légende", hint="Lancer `rushes meta`.")
    meta = read_meta(paths, post)
    account = config.account(post.account_id)
    brand = config.brand(account.brand)
    local = _moment(post, planned_at).astimezone(ZoneInfo(brand.timezone))
    publish_at = PublishAt(
        brand_local=f"{local:%Y-%m-%d %H:%M} {brand.timezone}",
        paris=f"{local.astimezone(PARIS):%Y-%m-%d %H:%M}",
    )
    earlier = earlier_parts(session, post, segment)[1] if segment.kind == "part" else []
    note = (
        f"Publier après la partie {earlier[-1][1]} ({post_key(earlier[-1][0].id)})"
        if earlier
        else None
    )
    episode = session.get_one(Episode, segment.episode_id)
    test = (
        (TEST_SERIES if episode.series_id is not None else TEST_CREATOR) if post.is_test else None
    )
    voice = _voice_is_current(paths, post)
    cover_frame_s = meta.cover_frame_ms / 1000
    return UploadFile(
        post_id=key,
        platform=account.platform,
        account=account.handle,
        publish_at=publish_at,
        order_note=note,
        files=UploadFiles(video=VIDEO_NAME, cover=COVER_NAME, cover_frame_s=cover_frame_s),
        caption=post.caption,
        hashtags=meta.hashtags,
        hook=UploadHook(applied=meta.hook_applied, alternatives=meta.hook_alternatives),
        settings=_settings(config, account.platform, voice=voice),
        mentions=[],
        campaign=None,
        checklist=_checklist(account.platform, cover_frame_s, test=test, voice=voice),
    )
