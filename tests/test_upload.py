"""`build_upload` (task 8b): the upload.json of a post, as in the example of docs/spec.md."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    CREATOR_ACCOUNT,
    packable_post,
)
from rushes.context import Context
from rushes.db.models import Post
from rushes.errors import RushesError
from rushes.ids import post_key
from rushes.package.upload import build_upload
from rushes.render.render import RENDER_VERSION
from rushes.schemas import UploadFile

SLOT = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)  # 15:00 in New York, 21:00 in Paris
SPEC_CAPTION = (
    "Tom really thought this plan would work 😭 Tom and Jerry, Part 2. Who wins? 👇 "
    "#fyp #nostalgiatok #cartoons #tomandjerry #slapstick"
)
SPEC_TAGS = ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstick"]
HOOKS = ("Wait for the ending 😭", ["Tom vs Jerry: who wins?", "He did NOT see that coming"])


def _upload(ctx: Context, post_id: int, **kwargs: Any) -> UploadFile:
    with Session(ctx.engine) as session:
        return build_upload(ctx.config, ctx.paths, session, post_id, **kwargs)


def _set(ctx: Context, post_id: int, **columns: Any) -> None:
    assignments = ", ".join(f"{name}=:{name}" for name in columns)
    with ctx.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE posts SET {assignments} WHERE id=:id"), {**columns, "id": post_id}
        )


def _two_parts(ctx: Context, delivery: Path, **meta: Any) -> tuple[int, int]:
    first = packable_post(ctx, delivery, part_number=1, span=(0, 60_000))
    with ctx.engine.connect() as conn:
        parent = conn.execute(
            text("SELECT episode_id FROM segments s JOIN posts p ON p.segment_id=s.id "
                 "WHERE p.id=:i"), {"i": first}
        ).scalar_one()  # fmt: skip
    second = packable_post(
        ctx, delivery, episode=parent, part_number=2, span=(60_000, 120_000), **meta
    )
    return first, second


def test_upload_json_matches_spec_example(ctx: Context, delivery: Path) -> None:
    first, second = _two_parts(
        ctx,
        delivery,
        caption=SPEC_CAPTION,
        hashtags=SPEC_TAGS,
        hook=HOOKS[0],
        alternatives=HOOKS[1],
        cover_frame_ms=3200,
    )
    upload = _upload(ctx, second, planned_at=SLOT)
    assert upload.model_dump() == {
        "post_id": post_key(second),
        "platform": "tiktok",
        "account": "@saturdaytoons",
        "publish_at": {
            "brand_local": "2026-11-17 15:00 America/New_York",
            "paris": "2026-11-17 21:00",
        },
        "order_note": f"Publier après la partie 1 ({post_key(first)})",
        "files": {"video": "video.mp4", "cover": "cover.jpg", "cover_frame_s": 3.2},
        "caption": SPEC_CAPTION,
        "hashtags": SPEC_TAGS,
        "hook": {"applied": HOOKS[0], "alternatives": HOOKS[1]},
        "settings": {
            "visibility": "public",
            "allow_comments": True,
            "allow_duet": False,
            "allow_stitch": False,
            "ai_generated_label": False,
            "branded_content": False,
        },
        "mentions": [],
        "campaign": None,
        "checklist": [
            "Couverture : image à 3,2 s ou cover.jpg",
            "Vérifier que la légende tient avant « plus »",
        ],
    }  # fmt: skip
    # same keys, same order as the example of the spec
    assert list(json.loads(upload.model_dump_json())) == [
        "post_id", "platform", "account", "publish_at", "order_note", "files", "caption",
        "hashtags", "hook", "settings", "mentions", "campaign", "checklist",
    ]  # fmt: skip


def test_upload_uses_the_planned_slot_of_the_row_and_requires_one(
    ctx: Context, delivery: Path
) -> None:
    post_id = packable_post(ctx, delivery)
    with pytest.raises(RushesError, match="créneau"):
        _upload(ctx, post_id)
    _set(ctx, post_id, planned_at=SLOT.isoformat(timespec="seconds"))
    assert _upload(ctx, post_id).publish_at.paris == "2026-11-17 21:00"


def test_upload_first_part_has_no_order_note(ctx: Context, delivery: Path) -> None:
    first, _ = _two_parts(ctx, delivery)
    assert _upload(ctx, first, planned_at=SLOT).order_note is None


def test_upload_order_note_skips_a_discarded_part_and_follows_ranks(
    ctx: Context, delivery: Path
) -> None:
    first, second = _two_parts(ctx, delivery)
    _set(ctx, first, status="discarded")  # R9: no gap, so part 2 is first in practice
    assert _upload(ctx, second, planned_at=SLOT).order_note is None
    # numbers not fixed (NULL): the rank among the visible parts is used
    _set(ctx, first, status="rendered", part_number=None)
    _set(ctx, second, part_number=None)
    assert _upload(ctx, second, planned_at=SLOT).order_note == (
        f"Publier après la partie 1 ({post_key(first)})"
    )


def test_upload_instagram_settings_and_checklist(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, owner=ACCOUNT_SAME_BRAND, cover_frame_ms=1500)
    upload = _upload(ctx, post_id, planned_at=SLOT)
    assert upload.platform == "instagram"
    assert upload.settings == {"share_to_feed": True}
    assert upload.checklist == ["Couverture : cover.jpg", "Partager dans le fil activé"]
    assert upload.files.cover_frame_s == 1.5


def test_upload_cover_second_uses_a_decimal_comma(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, cover_frame_ms=12_340)
    assert (
        _upload(ctx, post_id, planned_at=SLOT).checklist[0]
        == "Couverture : image à 12,3 s ou cover.jpg"
    )


def test_upload_creator_post_is_shown_in_paris_time(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, owner=CREATOR_ACCOUNT, creator_id="squeezie")
    upload = _upload(ctx, post_id, planned_at=datetime(2026, 11, 17, 17, 0, tzinfo=UTC))
    assert upload.publish_at.brand_local == "2026-11-17 18:00 Europe/Paris"
    assert upload.publish_at.paris == "2026-11-17 18:00"
    assert upload.account == "@instantculte"


def test_ai_label_from_current_voice_file(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    voice = ctx.paths.renders / f"{post_key(post_id)}.voice.json"
    _set(ctx, post_id, render_version=RENDER_VERSION)
    assert _upload(ctx, post_id, planned_at=SLOT).settings["ai_generated_label"] is False  # no file
    voice.write_text("{}", encoding="utf-8")
    upload = _upload(ctx, post_id, planned_at=SLOT)
    assert upload.settings["ai_generated_label"] is True
    assert list(upload.settings)[-1] == "branded_content"
    assert "contenu généré par IA" in upload.checklist[-1]
    _set(ctx, post_id, render_version="plate-0+ov-0")  # a voice made for an older render
    stale = _upload(ctx, post_id, planned_at=SLOT)
    assert stale.settings["ai_generated_label"] is False and len(stale.checklist) == 2


def test_ai_label_on_instagram_is_only_added_with_a_voice(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, owner=ACCOUNT_SAME_BRAND)
    _set(ctx, post_id, render_version=RENDER_VERSION)
    assert "ai_generated_label" not in _upload(ctx, post_id, planned_at=SLOT).settings
    (ctx.paths.renders / f"{post_key(post_id)}.voice.json").write_text("{}", encoding="utf-8")
    assert _upload(ctx, post_id, planned_at=SLOT).settings["ai_generated_label"] is True


def test_upload_test_post_starts_with_the_warning(ctx: Context, delivery: Path) -> None:
    series = packable_post(ctx, delivery, test=True)
    creator = packable_post(ctx, delivery, owner=CREATOR_ACCOUNT, creator_id="squeezie", test=True)
    assert _upload(ctx, series, planned_at=SLOT).checklist[0] == (
        "NE PAS PUBLIER : série sans licence (vitrine)"
    )
    assert _upload(ctx, creator, planned_at=SLOT).checklist[0] == (
        "NE PAS PUBLIER : créateur non autorisé"
    )
    assert len(_upload(ctx, series, planned_at=SLOT).checklist) == 3


def test_upload_needs_meta_that_agrees_with_the_row(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    _set(ctx, post_id, caption="another caption")
    with pytest.raises(RushesError, match="ne correspond pas"):
        _upload(ctx, post_id, planned_at=SLOT)
    (ctx.paths.renders / f"{post_key(post_id)}.meta.json").unlink()
    with pytest.raises(RushesError, match=r"meta\.json"):
        _upload(ctx, post_id, planned_at=SLOT)


def test_upload_without_a_caption_or_a_segment_is_refused(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery)
    _set(ctx, post_id, caption=None)
    with pytest.raises(RushesError, match="légende"):
        _upload(ctx, post_id, planned_at=SLOT)
    with Session(ctx.engine) as session:
        loose = Post(account_id=ACCOUNT, status="rendered", compilation_id=None)
        session.add(loose)
        session.commit()
        loose_id = loose.id
    with pytest.raises(RushesError, match="pas un passage"):
        _upload(ctx, loose_id, planned_at=SLOT)
    with pytest.raises(RushesError, match="introuvable"):
        _upload(ctx, 9999, planned_at=SLOT)


def test_upload_bytes_keep_unicode_and_are_deterministic(ctx: Context, delivery: Path) -> None:
    post_id = packable_post(ctx, delivery, caption="Ça y est 😭 « Tom » #fyp", hashtags=["#fyp"])
    first = _upload(ctx, post_id, planned_at=SLOT).model_dump_json(indent=1)
    assert first == _upload(ctx, post_id, planned_at=SLOT).model_dump_json(indent=1)
    assert "Ça y est 😭 « Tom »" in first and "\\u" not in first
