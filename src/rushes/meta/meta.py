"""`meta` step (task 8a): caption, hashtags and cover of a rendered post.

Files first (cover and meta.json, each through `staged`), then one short transaction: a guarded
UPDATE of the caption and hashtags, then the renames, then the commit (C46). The step only fills
posts whose caption is NULL: a caption made stale by a renumbering is task 16b's business.
"""

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, cast, get_args

from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rushes.assign.assign import default_layout
from rushes.config import Layout, Platform, Vertical
from rushes.context import Context, Filters
from rushes.core.rng import derive_seed
from rushes.core.text import hashtag_from_name
from rushes.db.derived import resolve
from rushes.db.models import Episode, Hook, Post, Segment
from rushes.errors import MediaError, RushesError
from rushes.fsutil import staged
from rushes.ids import episode_key, post_key
from rushes.media.frames import extract_frame
from rushes.media.probe import ProbeInfo, probe
from rushes.meta.caption import (
    build_caption,
    caption_problems,
    pick_question,
    template_source,
)
from rushes.meta.cover import cover_candidates, cover_words, render_cover, segment_keyframes
from rushes.meta.hashtags import default_pool, resolve_hashtags
from rushes.queue.numbering import part_number
from rushes.render.charter import BrandCharter, load_charter
from rushes.render.job import creator_title, subject_title
from rushes.runner import BaseStep, Outcome
from rushes.schemas import MetaFile, ScenesFile, read_json, write_json

MAX_CANDIDATES = 12  # key frames looked at per post, spread over the segment
SCORE_WIDTH = 320
COVER_SOURCE_WIDTH = 1080
DEFAULT_NOSTALGIA = "#nostalgia"


def _tag_list(raw: object) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(tag, str) for tag in raw):
        raise ValueError("liste de hashtags attendue")
    return list(raw)


def _tag(raw: object) -> str:
    if not isinstance(raw, str):
        raise ValueError("hashtag attendu")
    return raw


def _spread[T](items: Sequence[T], limit: int) -> list[T]:
    """At most `limit` items, evenly spread (the first and the last kept)."""
    if len(items) <= limit:
        return list(items)
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def _square_pixels(frame: Path, info: ProbeInfo) -> None:
    """Stretch a frame of anamorphic video to its displayed shape, in place."""
    with Image.open(frame) as image:
        width = round(image.width * info.display_width / info.width)
        if width == image.width:
            return
        fixed = image.resize((width, image.height), Image.Resampling.LANCZOS)
    fixed.save(frame, format="JPEG", quality=95)


class _Plan:
    """What `run` read from the database: plain values, the session is closed afterwards."""

    def __init__(self, ctx: Context, session: Session, post: Post, segment: Segment) -> None:
        self.post_id = post.id
        self.brand_id = ctx.config.brand_of(post.account_id).id
        self.seen_caption = post.caption
        self.seen_hook = post.hook_id
        self.seen_part = post.part_number
        self.seen_layout = post.layout
        self.span = (segment.start_ms, segment.end_ms)
        self.test = bool(post.is_test)
        episode = session.get_one(Episode, segment.episode_id)
        self.episode_id = episode.id
        account = ctx.config.account(post.account_id)
        brand = ctx.config.brand(account.brand)
        self.platform: Platform = account.platform
        self.language = brand.language
        vertical: Vertical = brand.vertical
        self.layout = self._layout(ctx, post)
        self.name = " ".join(subject_title(session, episode).split())  # flattened once
        if not self.name:
            raise RushesError(f"{post_key(post.id)} : titre vide (série ou créateur sans nom)")
        tokens = {"{nostalgia}": self._nostalgia(session)}
        self.credit: str | None = None
        if episode.series_id is not None:
            series = ctx.config.series_by_id(episode.series_id)
            subject_id, configured = (
                episode.series_id,
                (series.branding.hashtags_pool if series else []),
            )
            tokens["{series}"] = hashtag_from_name(self.name)
            pool_name = self.name
        else:
            subject_id = episode.creator_id or ""
            creator = ctx.config.creator_by_id(subject_id)
            if creator is None:
                raise RushesError(
                    f"{post_key(post.id)} : créateur {subject_id} absent de la config"
                )
            configured = []
            tokens["{creator}"] = creator.effective_hashtag
            pool_name = creator.effective_hashtag
            self.credit = _credit(creator.credit_template, creator_title(subject_id))
        self.subject_id = subject_id
        pool = resolve(
            session,
            "hashtags.pool",
            subject_id,
            config_value=configured or None,
            default=default_pool(pool_name, vertical),
            parse=_tag_list,
        ).value
        self.hashtags = resolve_hashtags(
            required=ctx.config.accounts.required_hashtags[vertical][self.platform],
            tokens=tokens,
            pool=pool,
            platform=self.platform,
            seed=derive_seed(post.id, "hashtags"),
        )
        hook = session.get(Hook, post.hook_id) if post.hook_id is not None else None
        self.hook = hook.text if hook is not None else None
        others = session.scalars(
            select(Hook.text)
            .where(Hook.segment_id == segment.id, Hook.id != (post.hook_id or 0))
            .order_by(Hook.rank.is_(None), Hook.rank, Hook.id)
        )
        self.alternatives = list(dict.fromkeys(others))
        # the number drawn on the video was fixed at render (C46); the rank is a fallback
        if post.part_number is not None:
            self.part: int | None = post.part_number
        else:
            self.part = part_number(session, post.id) if segment.kind == "part" else None

    @staticmethod
    def _layout(ctx: Context, post: Post) -> Layout:
        name = post.layout or default_layout(ctx.config)
        if name not in get_args(Layout):
            raise RushesError(f"{post_key(post.id)} : mise en page inconnue ({name})")
        return cast(Layout, name)

    @staticmethod
    def _nostalgia(session: Session) -> str:
        return resolve(
            session,
            "hashtags.nostalgia_instagram",
            "cartoons",
            config_value=None,
            default=DEFAULT_NOSTALGIA,
            parse=_tag,
        ).value


def _credit(template: str, name: str) -> str:
    """The creator's credit line: their name only (L19); campaign mentions come with task 26."""
    try:
        return template.format(name=name, mention=name).strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise RushesError(f"credit_template invalide ({exc}) : {template!r}") from exc


class MetaStep(BaseStep):
    name: ClassVar[str] = "meta"

    def key(self, item: int) -> str:
        return post_key(item)

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        """Rendered posts (render_version set) of an approved part without a caption.

        --force: every such post. A reset post (render_version NULL) waits for its re-render."""
        query = (
            select(Post.id)
            .join(Segment, Segment.id == Post.segment_id)
            .join(Episode, Episode.id == Segment.episode_id)
            .where(
                Post.status == "rendered",
                Post.render_version.is_not(None),  # NULL: waits for its re-render (16b, 36)
                Segment.status == "approved",
            )
            .order_by(Post.id)
        )
        if not filters.force:
            query = query.where(Post.caption.is_(None))
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
            if segment is None:
                return Outcome(
                    "skipped", f"{key} n'est pas un passage : métadonnées non gérées ici"
                )
            if post.status != "rendered":
                return Outcome("skipped", f"{key} est {post.status} : pas de métadonnées")
            if post.render_version is None:
                return Outcome("skipped", f"{key} attend son nouveau rendu : numéro non fixé")
            if segment.status != "approved":  # R3
                return Outcome("skipped", f"passage non approuvé : {key} sans métadonnées")
            if post.caption is not None and not force:
                return Outcome("skipped", f"déjà fait : {key} a une légende")
            plan = _Plan(ctx, session, post, segment)
        charters = ctx.config.charters or load_charter(ctx.paths.config_dir, ctx.config)
        charter = charters.get(plan.brand_id)
        if charter is None:
            raise RushesError(f"{key} : charte absente pour la marque {plan.brand_id}")

        caption = build_caption(
            platform=plan.platform,
            language=plan.language,
            hook=plan.hook,
            name=plan.name,
            part_number=plan.part,
            situation=None,
            question=pick_question(plan.language, derive_seed(item, "question")),
            credit=plan.credit,
            hashtags=plan.hashtags,
            test=plan.test,
            template=template_source(plan.language, ctx.paths.config_dir, plan.subject_id),
        )
        problems = caption_problems(caption, platform=plan.platform, name=plan.name)
        source = ctx.paths.episode_dir(episode_key(plan.episode_id)) / "clean.mp4"
        if not source.is_file():
            raise MediaError(
                f"{key} : {episode_key(plan.episode_id)}/clean.mp4 introuvable",
                hint="Relancer `rushes clean` pour cet épisode.",
            )
        cover = ctx.paths.renders / f"{key}.cover.jpg"
        meta = ctx.paths.renders / f"{key}.meta.json"
        ctx.paths.tmp.mkdir(parents=True, exist_ok=True)
        with (
            staged(cover, meta) as stage,
            tempfile.TemporaryDirectory(dir=ctx.paths.tmp) as scratch,
        ):
            frame_ms = self._cover(ctx, plan, charter, source, Path(scratch), stage.path(cover))
            write_json(
                MetaFile(
                    version=1,
                    post_id=item,
                    caption=caption,
                    hashtags=plan.hashtags,
                    cover_frame_ms=frame_ms - plan.span[0],
                    hook_applied=plan.hook,
                    hook_alternatives=plan.alternatives,
                ),
                stage.path(meta),
            )
            with ctx.engine.begin() as conn:  # compare-and-swap on what `run` read
                done = conn.execute(
                    text(
                        "UPDATE posts SET caption=:c, hashtags=:h WHERE id=:i "
                        "AND status='rendered' AND caption IS :seen AND hook_id IS :hook "
                        "AND part_number IS :part AND layout IS :layout "
                        "AND EXISTS (SELECT 1 FROM segments s WHERE s.id=posts.segment_id "
                        "AND s.status='approved')"
                    ),
                    {
                        "c": caption,
                        "h": json.dumps(plan.hashtags, ensure_ascii=False),
                        "i": item,
                        "seen": plan.seen_caption,
                        "hook": plan.seen_hook,
                        "part": plan.seen_part,
                        "layout": plan.seen_layout,
                    },
                )
                if done.rowcount != 1:
                    return Outcome("skipped", f"{key} a changé pendant le travail : fichiers jetés")
                stage.commit()
        note = (
            f"{len(plan.hashtags)} hashtags, couverture à {(frame_ms - plan.span[0]) / 1000:.1f} s"
        )
        if problems:
            return Outcome("warned", f"{note} ; à revoir : {' ; '.join(problems)}")
        return Outcome("done", note)

    @staticmethod
    def _cover(
        ctx: Context, plan: _Plan, charter: BrandCharter, source: Path, scratch: Path, out: Path
    ) -> int:
        """Pick the best key frame of the segment and write the cover; returns its time (ms)."""
        scenes: ScenesFile | None = None
        try:
            folder = ctx.paths.episode_dir(episode_key(plan.episode_id))
            scenes = read_json(ScenesFile, folder / "scenes.json")
        except OSError, ValueError:
            scenes = None  # no shots: the middle of the segment is the only candidate
        keyframes = _spread(segment_keyframes(scenes, plan.span), MAX_CANDIDATES)
        if not keyframes:
            keyframes = [((plan.span[0] + plan.span[1]) // 2, 0.0)]
        frames: list[tuple[int, Path]] = []
        for t_ms, _ in keyframes:
            path = scratch / f"candidate-{t_ms}.jpg"
            try:
                extract_frame(source, t_ms, path, width=SCORE_WIDTH)
            except MediaError:
                continue  # a keyframe past the end of the file
            frames.append((t_ms, path))
        ranked = cover_candidates(frames, motion=dict(keyframes))
        if not ranked:
            raise MediaError(
                f"{post_key(plan.post_id)} : aucune image exploitable pour la couverture"
            )
        best_ms = ranked[0][0]
        best = scratch / "best.jpg"
        extract_frame(source, best_ms, best, width=COVER_SOURCE_WIDTH)
        _square_pixels(best, probe(source))
        render_cover(
            best,
            layout=plan.layout,
            words=cover_words(plan.hook, plan.name),
            part_number=plan.part,
            charter=charter,
            language=plan.language,
            out=out,
        )
        return best_ms
