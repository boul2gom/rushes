"""Module `base` (R9, R50): banner, hook, progress bar and end card, drawn with Pillow.

`job_texts` is the one place that decides which texts are drawn; `plan` draws exactly those.
All overlays stay inside the safe margins of the charter (R9)."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from PIL import Image, ImageDraw, ImageOps

from rushes.config import Layout, Originality
from rushes.errors import RushesError
from rushes.render.canvas import CANVAS_H, CANVAS_W, Rect, video_rect
from rushes.render.charter import BrandCharter
from rushes.render.text import fit_text

HOOK_END_MS = 2500
END_CARD_MS = 2000
AVATAR_PX = 240
BANNER_TOP, BANNER_H = 230, 160
HOOK_TOP, HOOK_H = 460, 200
CARD_W, CARD_PAD, CARD_GAP = 800, 28, 24
SCRIM = (0, 0, 0, 150)  # translucent black behind the end card
ACCENT_PX = 8  # banner underline in the highlight colour


@dataclass(frozen=True)
class RenderJob:
    post_id: int
    account_id: str
    handle: str
    language: str
    title: str
    layout: Layout
    duration_ms: int
    part_number: int | None
    bestof_title: str | None
    hook_text: str | None
    next_label: str | None
    is_last_part: bool
    charter: BrandCharter
    avatar: Path
    src_w: int
    src_h: int


@dataclass(frozen=True)
class Overlay:
    png: Path
    x: int
    y: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class AudioLayer:
    wav: Path
    start_ms: int
    duck_db: float


@dataclass(frozen=True)
class RenderPlan:
    overlays: list[Overlay]
    audio_layers: list[AudioLayer]
    progress_bar: bool
    ai_voice: bool


class OriginalityModule(Protocol):
    name: ClassVar[Originality]

    def plan(self, job: RenderJob, workdir: Path) -> RenderPlan: ...


@dataclass(frozen=True)
class JobTexts:
    """Every text of the overlays, in drawing order."""

    banner: str
    hook: str | None
    handle: str
    subscribe: str
    next_line: str | None

    def all(self) -> list[str]:
        parts = [self.banner, self.hook, self.handle, self.subscribe, self.next_line]
        return [t for t in parts if t]


def job_texts(job: RenderJob) -> JobTexts:
    copy = job.charter.texts[job.language]
    if job.part_number is not None:
        banner = copy.banner.format(title=job.title, n=job.part_number)
    else:  # best-of and compilations: the title alone, no number (R9)
        banner = job.bestof_title or job.title
    next_line = job.next_label if job.part_number is not None else None
    hook = (job.hook_text or "").strip() or None  # a blank hook is no hook
    return JobTexts(banner, hook, job.handle, copy.subscribe, next_line)


def _luma(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b


def _disc(path: Path, size: int) -> Image.Image:
    """The brand picture cropped to a disc (anti-aliased by drawing the mask at 4x)."""
    try:
        with Image.open(path) as source:
            square = ImageOps.fit(source.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    except (OSError, SyntaxError, ValueError) as exc:  # damaged after the config was read
        raise RushesError(f"photo de profil illisible ({path.name}) : {exc}") from exc
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    square.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
    return square


def _rounded(
    size: tuple[int, int], fill: str | tuple[int, int, int, int], radius: int
) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill)
    return image


class BaseModule:
    name: ClassVar[Originality] = "base"

    def plan(self, job: RenderJob, workdir: Path) -> RenderPlan:
        workdir.mkdir(parents=True, exist_ok=True)
        texts = job_texts(job)
        overlays = [self._banner(job, texts.banner, workdir)]
        if texts.hook:
            overlays.append(self._hook(job, texts.hook, workdir))
        overlays += self._end_card(job, texts, workdir)
        return RenderPlan(overlays, audio_layers=[], progress_bar=True, ai_voice=False)

    @staticmethod
    def _text(
        job: RenderJob,
        text: str,
        box: Rect,
        max_px: int,
        min_px: int,
        *,
        max_lines: int = 2,
        fill: str = "#FFFFFF",
        stroke_px: int = 0,
        stroke_fill: str = "#000000",
    ) -> Image.Image:
        charter = job.charter
        return fit_text(
            text,
            font=charter.font_path(),
            emoji_font=charter.emoji_font_path(),
            box=box,
            max_px=max_px,
            min_px=min_px,
            max_lines=max_lines,
            fill=fill,
            stroke_px=stroke_px,
            stroke_fill=stroke_fill,
        )

    def _banner(self, job: RenderJob, text: str, workdir: Path) -> Overlay:
        safe, charter = job.charter.safe, job.charter
        box = Rect(safe.left, BANNER_TOP, CANVAS_W - safe.left - safe.right, BANNER_H)
        pad = 24
        image = _rounded((box.w, box.h), charter.banner_bg, 28)
        label = self._text(
            job,
            text,
            Rect(0, 0, box.w - 2 * pad, box.h - 2 * pad - ACCENT_PX),
            max_px=96,
            min_px=40,
            fill=charter.banner_fg,
        )
        image.alpha_composite(
            label, ((box.w - label.width) // 2, (box.h - ACCENT_PX - label.height) // 2)
        )
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (pad, box.h - pad // 2 - ACCENT_PX, box.w - pad, box.h - pad // 2),
            ACCENT_PX // 2,
            fill=charter.highlight,
        )
        path = workdir / "banner.png"
        image.save(path)
        return Overlay(path, box.x, box.y, 0, job.duration_ms)

    def _hook(self, job: RenderJob, text: str, workdir: Path) -> Overlay:
        safe = job.charter.safe
        box = Rect(safe.left, HOOK_TOP, CANVAS_W - safe.left - safe.right, HOOK_H)
        image = self._text(
            job,
            text,
            box,
            max_px=88,
            min_px=44,
            fill="#FFFFFF",
            stroke_px=6,
            stroke_fill=job.charter.banner_bg,
        )
        path = workdir / "hook.png"
        image.save(path)
        x = box.x + (box.w - image.width) // 2
        y = box.y + (box.h - image.height) // 2
        return Overlay(path, x, y, 0, min(HOOK_END_MS, job.duration_ms))

    def _end_card(self, job: RenderJob, texts: JobTexts, workdir: Path) -> list[Overlay]:
        charter, safe = job.charter, job.charter.safe
        width = min(CARD_W, CANVAS_W - safe.left - safe.right)
        inner = width - 2 * CARD_PAD
        start = max(0, job.duration_ms - END_CARD_MS)

        handle = self._text(
            job,
            texts.handle,
            Rect(0, 0, inner, 90),
            72,
            40,
            max_lines=1,
            fill=charter.banner_fg,
            stroke_px=3,
            stroke_fill=charter.banner_bg,
        )
        label = self._text(
            job,
            texts.subscribe,
            Rect(0, 0, inner - 80, 64),
            56,
            32,
            max_lines=1,
            fill="#000000" if _luma(charter.highlight) > 140 else "#FFFFFF",
        )
        button = _rounded((label.width + 80, 96), charter.highlight, 48)
        button.alpha_composite(label, (40, (96 - label.height) // 2))
        line = (
            self._text(
                job,
                texts.next_line,
                Rect(0, 0, inner, 130),
                56,
                34,
                fill=charter.banner_fg,
                stroke_px=3,
                stroke_fill=charter.banner_bg,
            )
            if texts.next_line
            else None
        )
        avatar = _disc(job.avatar, AVATAR_PX)

        parts: list[tuple[str, Image.Image]] = [
            ("avatar", avatar),
            ("handle", handle),
            ("button", button),
        ]
        if line is not None:
            parts.append(("next", line))
        total = 2 * CARD_PAD + sum(p.height for _, p in parts) + CARD_GAP * (len(parts) - 1)

        rect = video_rect(job.layout, job.src_w, job.src_h)
        left = min(max(rect.x + rect.w // 2 - width // 2, safe.left), CANVAS_W - safe.right - width)
        # never over the banner (a wide picture leaves a short zone), nor under the bottom margin
        low = max(safe.top, BANNER_TOP + BANNER_H + CARD_GAP)
        high = CANVAS_H - safe.bottom - total
        top = min(max(rect.y + rect.h // 2 - total // 2, low), max(low, high))

        scrim = workdir / "card.png"
        _rounded((width, total), SCRIM, 40).save(scrim)
        overlays = [Overlay(scrim, left, top, start, job.duration_ms)]
        y = top + CARD_PAD
        for name, image in parts:
            path = workdir / f"{name}.png"
            image.save(path)
            x = left + (width - image.width) // 2
            overlays.append(Overlay(path, x, y, start, job.duration_ms))
            y += image.height + CARD_GAP
        return overlays
