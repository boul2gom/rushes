"""Overlays of the `base` module (task 7b): banner, hook, end card, R9 safe zone and numbers."""

import dataclasses
import re
import time
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont
from PIL import Image

import rushes.originality.base as base_module
from rushes.config import Layout
from rushes.originality import module_for
from rushes.originality.base import BaseModule, RenderJob, RenderPlan
from rushes.render.canvas import Rect
from rushes.render.charter import BrandCharter, Copy, SafeMargins, avatar_path, load_charter
from rushes.render.overlays import overlay_digest, overlay_texts
from rushes.render.text import ELLIPSIS, TextLayout, _clusters, fit_text, plan_text

TOTAL = re.compile(r"\d+\s*(/|of|sur)\s*\d+")  # R9: a total of parts, in any wording
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"
LONG_TITLE = "Les Zinzins de l'Espace 🚀 — Partie 12"


@pytest.fixture(scope="module")
def charters() -> dict[str, BrandCharter]:
    return load_charter(REPO_CONFIG)


def make_job(
    charters: dict[str, BrandCharter],
    brand: str = "saturday_toons",
    *,
    language: str = "en",
    **changes: object,
) -> RenderJob:
    job = RenderJob(
        post_id=7,
        account_id="st_tt",
        handle="@saturdaytoons",
        language=language,
        title="Tom and Jerry",
        layout="square",
        duration_ms=30_000,
        part_number=2,
        bestof_title=None,
        hook_text="Wait for the end",
        next_label="Part 3 next",
        is_last_part=False,
        charter=charters[brand],
        avatar=avatar_path(REPO_CONFIG, brand),
        src_w=1280,
        src_h=720,
    )
    return dataclasses.replace(job, **changes)


def jobs_for_parts(charters: dict[str, BrandCharter], *numbers: int, last: int) -> list[RenderJob]:
    """One job per part number; the part `last` is the last one (names another series)."""
    out = []
    for n in numbers:
        label = "Watch Oggy next" if n == last else f"Part {n + 1} next"
        out.append(make_job(charters, part_number=n, next_label=label, is_last_part=n == last))
    return out


def inside_safe(plan: RenderPlan, safe: SafeMargins) -> list[str]:
    """Overlays that leave the safe zone of the charter (R9)."""
    bad = []
    for o in plan.overlays:
        w, h = Image.open(o.png).size
        ok = (
            o.x >= safe.left
            and o.x + w <= 1080 - safe.right
            and o.y >= safe.top
            and o.y + h <= 1920 - safe.bottom
        )
        if not ok:
            bad.append(f"{o.png.name} {o.x},{o.y} {w}x{h}")
    return bad


# --- R9: no total, safe zone ---------------------------------------------------------------


def test_r9_total_regex_catches_every_wording() -> None:
    for text in ("Part 1/3", "Part 2 of 5", "Partie 2 sur 4", "2 / 9"):
        assert TOTAL.search(text), text
    for text in ("Part 12", "Tom and Jerry — Part 3", "Partie 3 ensuite", "@toontape"):
        assert not TOTAL.search(text), text


def test_r9_total_parts_never_shown(charters: dict[str, BrandCharter]) -> None:
    for job in jobs_for_parts(charters, 1, 2, 3, last=3):
        assert not any(TOTAL.search(t) for t in overlay_texts(job)), overlay_texts(job)
    for brand, language in (("instant_culte", "fr"), ("peak_moments", "en")):
        job = make_job(charters, brand, language=language, part_number=12)
        assert not any(TOTAL.search(t) for t in overlay_texts(job))


def test_r9_a_template_with_a_total_is_caught_by_the_same_check(
    charters: dict[str, BrandCharter],
) -> None:
    """Negative control: the check above is not vacuous."""
    charter = charters["saturday_toons"]
    bad = Copy(**{**charter.texts["en"].model_dump(), "banner": "{title} — Part {n} of 3"})
    job = make_job(charters, charter=charter.model_copy(update={"texts": {"en": bad}}))
    assert any(TOTAL.search(t) for t in overlay_texts(job))
    for wording in ("{title} {n}/3", "{title} {n} sur 3"):
        bad = Copy(**{**charter.texts["en"].model_dump(), "banner": wording})
        job = make_job(charters, charter=charter.model_copy(update={"texts": {"en": bad}}))
        assert any(TOTAL.search(t) for t in overlay_texts(job)), wording


def test_r9_text_inside_safe_margins(charters: dict[str, BrandCharter], tmp_path: Path) -> None:
    plan = BaseModule().plan(make_job(charters), tmp_path)
    for o in plan.overlays:
        w, h = Image.open(o.png).size
        assert o.x >= 60 and o.x + w <= 1080 - 140 and o.y >= 150 and o.y + h <= 1920 - 480
    assert inside_safe(plan, SafeMargins()) == []


@pytest.mark.parametrize("layout", ["square", "portrait", "full", "vertical"])
@pytest.mark.parametrize("src", [(1280, 720), (720, 1280), (2560, 1080)])
def test_r9_every_layout_keeps_every_overlay_in_the_safe_zone(
    charters: dict[str, BrandCharter], tmp_path: Path, layout: Layout, src: tuple[int, int]
) -> None:
    job = make_job(
        charters,
        layout=layout,
        src_w=src[0],
        src_h=src[1],
        title="Un titre vraiment très long qui n'en finit pas de continuer " * 2,
        hook_text="Une accroche interminable " * 8,
        next_label="À voir ensuite : " + "Série " * 12,
    )
    plan = BaseModule().plan(job, tmp_path)
    assert inside_safe(plan, job.charter.safe) == []


def test_the_end_card_never_covers_the_banner_on_a_wide_picture(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    """`full` with a 2.39:1 source leaves a short zone (1080x452): the card stays under it."""
    job = make_job(charters, layout="full", src_w=2560, src_h=1080)
    plan = BaseModule().plan(job, tmp_path)
    scrim = next(o for o in plan.overlays if o.png.name == "card.png")
    banner = plan.overlays[0]
    assert scrim.y >= banner.y + Image.open(banner.png).height + 24  # a gap under the banner
    assert inside_safe(plan, job.charter.safe) == []


def test_r9_safe_margins_come_from_the_charter(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    wide = SafeMargins(top=200, bottom=600, left=100, right=200)
    charter = charters["saturday_toons"].model_copy(update={"safe": wide})
    plan = BaseModule().plan(make_job(charters, charter=charter), tmp_path)
    assert inside_safe(plan, wide) == []
    assert inside_safe(plan, SafeMargins(top=400, bottom=480, left=60, right=140)) != []  # control


def test_r9_safe_zone_check_flags_an_overlay_outside(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    """Negative control of the helper: an overlay on the right edge or at the bottom is flagged."""
    plan = BaseModule().plan(make_job(charters), tmp_path)
    moved = dataclasses.replace(plan.overlays[0], x=1000)
    low = dataclasses.replace(plan.overlays[0], y=1900)
    assert inside_safe(dataclasses.replace(plan, overlays=[moved, low]), SafeMargins()) != []


# --- banner, hook, texts ---------------------------------------------------------------------


def test_banner_long_unicode_title_fits_safe_zone(tmp_path: Path) -> None:
    charters = load_charter(REPO_CONFIG)
    job = make_job(
        charters,
        "instant_culte",
        language="fr",
        title="Les Zinzins de l'Espace 🚀",
        part_number=12,
        handle="@instantculte",
    )
    assert "Les Zinzins de l'Espace 🚀 — Partie 12" in overlay_texts(job)
    plan = BaseModule().plan(job, tmp_path)
    banner = plan.overlays[0]
    assert Image.open(banner.png).size == (880, 160)
    assert (banner.x, banner.y) == (60, 230)
    assert inside_safe(plan, job.charter.safe) == []
    # the text really is on the banner (not an empty band): pixels of the foreground colour
    pixels = Image.open(banner.png).convert("RGB").get_flattened_data()
    assert sum(1 for p in pixels if p == (0xF5, 0xF1, 0xE8)) > 300


def test_banner_is_in_the_top_third_above_the_video_zone(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    banner = BaseModule().plan(make_job(charters), tmp_path).overlays[0]
    assert banner.y + Image.open(banner.png).height <= 420  # the video zone starts at 420
    assert banner.y < 1920 // 3
    assert (banner.start_ms, banner.end_ms) == (0, 30_000)


def test_banner_text_uses_the_part_number_of_the_language(
    charters: dict[str, BrandCharter],
) -> None:
    en = make_job(charters, part_number=4)
    fr = make_job(charters, "instant_culte", language="fr", part_number=4, title="Squeezie")
    assert overlay_texts(en)[0] == "Tom and Jerry — Part 4"
    assert overlay_texts(fr)[0] == "Squeezie — Partie 4"


def test_bestof_has_no_part_number(charters: dict[str, BrandCharter], tmp_path: Path) -> None:
    job = make_job(
        charters,
        part_number=None,
        bestof_title="Tom and Jerry : the best moments",
        next_label=None,
        hook_text=None,
    )
    texts = overlay_texts(job)
    assert texts[0] == "Tom and Jerry : the best moments"  # the title alone
    assert not any(re.search(r"\b(Part|Partie)\b", t) for t in texts)
    assert not any(re.search(r"\d", t) for t in texts)


def test_hook_is_drawn_for_2500_ms_just_under_the_banner(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    plan = BaseModule().plan(make_job(charters), tmp_path)
    hook = next(o for o in plan.overlays if o.png.name == "hook.png")
    w, h = Image.open(hook.png).size
    assert (hook.start_ms, hook.end_ms) == (0, 2500)
    assert hook.y >= 460 and hook.y + h <= 660 and hook.x >= 60 and hook.x + w <= 940
    assert "Wait for the end" in overlay_texts(make_job(charters))


def test_hook_ends_with_a_very_short_video(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    plan = BaseModule().plan(make_job(charters, duration_ms=1800), tmp_path)
    hook = next(o for o in plan.overlays if o.png.name == "hook.png")
    assert hook.end_ms == 1800


def test_no_hook_overlay_without_a_hook_text(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(charters, hook_text=None)
    plan = BaseModule().plan(job, tmp_path)
    assert all(o.png.name != "hook.png" for o in plan.overlays)
    assert len(overlay_texts(job)) == 4  # banner, handle, subscribe, next line


# --- end card --------------------------------------------------------------------------------


def test_end_card_shows_avatar_handle_and_subscribe(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(charters)
    texts = overlay_texts(job)
    assert job.handle in texts and "Follow" in texts
    assert sum(t.count("@") for t in texts if t == job.handle) == 1  # a single @
    plan = BaseModule().plan(job, tmp_path)
    avatar = [o for o in plan.overlays if Image.open(o.png).size == (240, 240)]
    assert len(avatar) == 1 and avatar[0].png.name == "avatar.png"
    disc = Image.open(avatar[0].png)
    assert disc.mode == "RGBA" and disc.getpixel((0, 0))[3] == 0  # a disc: corners transparent
    assert disc.getpixel((120, 120))[3] == 255
    ordered = [o for o in plan.overlays if o.start_ms == 28_000]
    names = [o.png.name for o in ordered]
    assert names == ["card.png", "avatar.png", "handle.png", "button.png", "next.png"]
    ys = [o.y for o in ordered[1:]]
    assert ys == sorted(ys)  # avatar, handle, button, then the next line, top to bottom


def test_end_card_lasts_the_last_2000_ms_on_every_post(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    for duration, start in ((30_000, 28_000), (5_000, 3_000), (2_000, 0), (1_500, 0)):
        job = make_job(charters, duration_ms=duration, part_number=None, next_label=None)
        plan = BaseModule().plan(job, tmp_path / f"d{duration}")
        card = [o for o in plan.overlays if o.png.name in ("avatar.png", "button.png")]
        assert {(o.start_ms, o.end_ms) for o in card} == {(start, duration)}


def test_end_card_is_centred_on_the_video_zone(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    plan = BaseModule().plan(make_job(charters, layout="square"), tmp_path)
    scrim = next(o for o in plan.overlays if o.png.name == "card.png")
    w, h = Image.open(scrim.png).size
    assert abs(scrim.y + h / 2 - (420 + 1080 / 2)) <= 1  # centre of the square zone
    assert abs(scrim.x + w / 2 - 540) <= 1
    # a taller zone: the card is pushed up so that it stays above the bottom margin (R9)
    tall = BaseModule().plan(make_job(charters, layout="portrait"), tmp_path / "tall")
    scrim = next(o for o in tall.overlays if o.png.name == "card.png")
    assert scrim.y + h <= 1920 - 480


def test_end_card_subscribe_in_brand_language(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(
        charters,
        "instant_culte",
        language="fr",
        handle="@instantculte",
        next_label="Partie 3 ensuite",
    )
    texts = overlay_texts(job)
    assert "Abonne-toi" in texts and "Follow" not in texts
    assert not any("Follow" in t for t in texts)


def test_last_part_end_card_points_to_other_series(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(charters, part_number=5, is_last_part=True, next_label="Watch Oggy next")
    texts = overlay_texts(job)
    assert "Watch Oggy next" in texts
    assert not any(re.search(r"Part \d+ next", t) for t in texts)  # no announced next part
    plan = BaseModule().plan(job, tmp_path)
    assert any(o.png.name == "next.png" for o in plan.overlays)


def test_bestof_end_card_has_no_next_line(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(charters, part_number=None, bestof_title="Best of", next_label="Part 3 next")
    plan = BaseModule().plan(job, tmp_path)  # even a stray label is not drawn without a number
    assert all(o.png.name != "next.png" for o in plan.overlays)
    assert overlay_texts(job) == ["Best of", "Wait for the end", "@saturdaytoons", "Follow"]
    assert {o.png.name for o in plan.overlays if o.start_ms == 28_000} == {
        "card.png",
        "avatar.png",
        "handle.png",
        "button.png",
    }


def test_overlay_texts_is_the_single_source_of_what_is_drawn(
    charters: dict[str, BrandCharter], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn: list[str] = []
    real = base_module.fit_text

    def spy(text: str, **kwargs: object) -> Image.Image:
        drawn.append(text)
        return real(text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(base_module, "fit_text", spy)
    for job in (
        make_job(charters),
        make_job(charters, part_number=None, bestof_title="Best of", next_label=None),
        make_job(charters, hook_text=None),
    ):
        drawn.clear()
        BaseModule().plan(job, tmp_path)
        assert sorted(drawn) == sorted(overlay_texts(job))


# --- module registry -------------------------------------------------------------------------


def test_module_for_base_by_default() -> None:
    module = module_for("base")
    assert isinstance(module, BaseModule) and module.name == "base"


def test_module_for_advanced_waits_for_task_35() -> None:
    with pytest.raises(NotImplementedError, match="tâche 35"):
        module_for("advanced")


def test_base_plan_has_the_bar_and_no_voice(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    plan = BaseModule().plan(make_job(charters), tmp_path)
    assert plan.progress_bar and not plan.ai_voice and plan.audio_layers == []


# --- fit_text --------------------------------------------------------------------------------

FONTS = REPO_CONFIG.parent / "src" / "rushes" / "assets" / "fonts"
MAIN, EMOJI = FONTS / "LuckiestGuy-Regular.ttf", FONTS / "NotoEmoji-Regular.ttf"
BOX = Rect(0, 0, 800, 160)


def _fit(text: str, box: Rect = BOX, **kw: int) -> Image.Image:
    return fit_text(
        text, font=MAIN, emoji_font=EMOJI, box=box, max_px=kw.get("max_px", 96),
        min_px=kw.get("min_px", 40), max_lines=kw.get("max_lines", 2),
    )  # fmt: skip


def _ink(image: Image.Image) -> int:
    return sum(1 for a in image.getchannel("A").get_flattened_data() if a > 0)


def _alpha_box(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.getchannel("A").getbbox()
    assert box is not None
    return box


def test_fit_text_short_text_stays_on_one_line_at_full_size() -> None:
    image = _fit("Hi")
    assert image.mode == "RGBA" and _ink(image) > 0
    assert image.width <= BOX.w
    _, top, _, bottom = _alpha_box(image)
    assert bottom - top > 60  # cap height of a 96 px font: not shrunk
    assert image.height < 110  # one line, not two


@pytest.mark.parametrize(
    "text",
    [
        "Short",
        "Un titre moyen pour tester le rétrécissement",
        "Un titre vraiment très long qui demande deux lignes de texte entières ici",
        "Mot" * 60,
        "Un titre absurdement long " * 20,
        "🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀",
        "Les Zinzins de l'Espace 🚀 — Partie 12",
        "Édition spéciale Été Ça 😱",
    ],
)
def test_fit_text_never_exceeds_the_box(text: str) -> None:
    image = _fit(text)
    assert image.width <= BOX.w and image.height <= BOX.h


def test_fit_text_shrinks_before_adding_a_line() -> None:
    short = _fit("Hi")  # one line at 96 px
    medium = _fit("Un titre moyen pour")
    assert 0 < medium.height < short.height  # still one line, in a smaller font
    two = _fit("Un titre vraiment très long qui demande deux lignes")
    assert two.height >= 2 * 40  # then two lines (each at least min_px high)


def _plan(text: str, *, max_px: int = 96, min_px: int = 40, max_lines: int = 2) -> TextLayout:
    return plan_text(
        text,
        font=MAIN,
        emoji_font=EMOJI,
        box=BOX,
        max_px=max_px,
        min_px=min_px,
        max_lines=max_lines,
    )


def test_plan_text_marks_a_cut_text_and_it_ends_with_the_ellipsis() -> None:
    kept = _plan("Un titre vraiment très long qui demande deux lignes")
    assert not kept.truncated and not kept.lines[-1].endswith(ELLIPSIS)
    cut = _plan("Un titre absurdement long " * 20)
    assert cut.truncated and cut.size == 40 and len(cut.lines) <= 2
    assert cut.lines[-1].endswith(ELLIPSIS)
    assert cut.lines[0].startswith("Un titre")
    one = _plan("Un titre absurdement long " * 20, max_lines=1)
    assert one.truncated and len(one.lines) == 1 and one.lines[0].endswith(ELLIPSIS)


def test_plan_text_cuts_at_a_word_boundary_when_a_word_fits() -> None:
    cut = _plan("Un titre absurdement long " * 20, max_lines=1)
    body = cut.lines[0].removesuffix(ELLIPSIS)
    assert body.split()[-1] in {"Un", "titre", "absurdement", "long"}  # a whole word, no stump
    one_word = _plan(
        "A" * 400, max_px=40, min_px=40, max_lines=1
    )  # no word to keep: cut by symbols
    assert one_word.truncated and one_word.lines[0].endswith(ELLIPSIS)
    assert set(one_word.lines[0].removesuffix(ELLIPSIS)) == {"A"}


def test_fit_text_a_thousand_characters_is_fast() -> None:
    start = time.perf_counter()
    image = _fit("mot " * 250, max_lines=2)
    assert time.perf_counter() - start < 1.5  # binary search, not one rewrap per character
    assert image.width <= BOX.w and image.height <= BOX.h


def test_clusters_keep_a_visible_symbol_in_one_piece() -> None:
    assert _clusters("e\u0301x") == ["e\u0301", "x"]  # base and combining mark
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert _clusters(f"a{family}b") == ["a", family, "b"]  # a ZWJ sequence
    assert _clusters("\U0001f1eb\U0001f1f7\U0001f1e9\U0001f1ea") == [
        "\U0001f1eb\U0001f1f7",
        "\U0001f1e9\U0001f1ea",
    ]  # two flags, not four loose letters
    assert _clusters("\U0001f44d\U0001f3fd!") == ["\U0001f44d\U0001f3fd", "!"]  # skin tone


def test_a_cut_never_splits_a_joined_emoji() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    cut = _plan(family * 40, max_px=40, min_px=40, max_lines=1)
    assert cut.truncated
    kept = cut.lines[0].removesuffix(ELLIPSIS)
    assert kept.count(family) * len(family) == len(kept)  # whole families only


def test_fit_text_keeps_a_zwj_sequence_in_one_glyph() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    separate = _fit("\U0001f468\U0001f469\U0001f467")
    joined = _fit(family)
    assert joined.width < separate.width * 0.6  # one family glyph, not three faces


def test_fit_text_uses_the_emoji_font_for_a_character_the_font_lacks() -> None:
    with TTFont(MAIN) as ttf:
        assert ord("\U0001f680") not in (ttf.getBestCmap() or {})
    with TTFont(EMOJI) as ttf:
        assert ord("\U0001f680") in (ttf.getBestCmap() or {})
    plain = _fit("A")
    with_emoji = _fit("A\U0001f680")
    assert with_emoji.width > plain.width + 20  # the glyph was drawn, not dropped
    right = with_emoji.crop((plain.width + 5, 0, with_emoji.width, with_emoji.height))
    assert _ink(right) > 100


def test_fit_text_draws_the_emoji_in_bold() -> None:
    from PIL import ImageDraw, ImageFont

    light = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    font = ImageFont.truetype(str(EMOJI), 100)  # the variable font's default weight (400)
    ImageDraw.Draw(light).text((20, 200), "\U0001f680", font=font, anchor="ls", fill="#FFFFFF")
    bold = _fit("\U0001f680", box=Rect(0, 0, 300, 300), max_px=100, min_px=100)
    assert _ink(bold) > _ink(light) * 1.05


def test_fit_text_draws_accents_with_the_main_font() -> None:
    with TTFont(MAIN) as ttf:
        cmap = ttf.getBestCmap() or {}
    assert all(ord(c) in cmap for c in "éàÀ—…")
    assert _ink(_fit("À voir ensuite : é")) > 0


def test_fit_text_does_not_clip_an_accent_taller_than_the_font_ascent() -> None:
    """Luckiest Guy's capitals with an accent reach above its hhea ascent (I1 of the review)."""
    wide = Rect(0, 0, 800, 400)
    accented = _alpha_box(_fit("\u00c9", box=wide, max_px=100, min_px=100))
    plain = _alpha_box(_fit("E", box=wide, max_px=100, min_px=100))
    assert accented[3] - accented[1] > plain[3] - plain[1] + 10  # the accent is there


def test_fit_text_keeps_a_leading_emoji_and_accents_off_the_first_row() -> None:
    for text in ("\U0001f631 wow", "\u00c9dition \u00c0 \u00c9t\u00e9", "e\u0301t\u00e9"):
        left, top, right, bottom = _alpha_box(_fit(text, max_px=96))
        image = _fit(text, max_px=96)
        assert top >= 1 and bottom <= image.height - 1, text  # no ink on the edge rows
        assert left >= 1 and right <= image.width - 1, text


def test_fit_text_normalises_to_nfc() -> None:
    nfd = _fit("e\u0301t\u00e9")  # e + combining acute: the main font has no U+0301
    nfc = _fit("\u00e9t\u00e9")
    assert nfd.size == nfc.size and nfd.tobytes() == nfc.tobytes()


def test_fit_text_rejects_inverted_sizes() -> None:
    with pytest.raises(ValueError, match="tailles"):
        _fit("x", max_px=20, min_px=40)


def test_fit_text_rejects_a_box_too_small_for_one_line() -> None:
    with pytest.raises(ValueError, match="trop petite"):
        _fit("Hello", box=Rect(0, 0, 10, 10))
    with pytest.raises(ValueError, match="trop petite"):
        _fit("Hello", box=Rect(0, 0, 800, 20), min_px=40)


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_fit_text_rejects_a_blank_text(blank: str) -> None:
    with pytest.raises(ValueError, match="texte vide"):
        _fit(blank)


# --- the digest of what is drawn (16b stores it) ------------------------------------------------


def test_overlay_digest_follows_what_is_written_on_the_video(
    charters: dict[str, BrandCharter],
) -> None:
    job = make_job(charters)
    digest = overlay_digest(job)
    assert re.fullmatch(r"[0-9a-f]{40}", digest) and overlay_digest(make_job(charters)) == digest
    # every change a viewer could see changes it: number, next line, hook, handle, title
    changed = [
        make_job(charters, part_number=3, next_label="Part 4 next"),
        make_job(charters, is_last_part=True, next_label="Watch Oggy next"),
        make_job(charters, next_label="Follow for more", is_last_part=True),
        make_job(charters, hook_text="Another hook"),
        make_job(charters, hook_text=None),
        make_job(charters, handle="@other"),
        make_job(charters, title="Oggy"),
    ]
    assert len({overlay_digest(j) for j in [job, *changed]}) == len(changed) + 1
    # what is not drawn does not change it
    assert overlay_digest(make_job(charters, duration_ms=12_345, post_id=99)) == digest


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_a_blank_hook_is_no_hook(
    charters: dict[str, BrandCharter], tmp_path: Path, blank: str
) -> None:
    job = make_job(charters, hook_text=blank)
    plan = BaseModule().plan(job, tmp_path)  # no ValueError at render time
    assert all(o.png.name != "hook.png" for o in plan.overlays)
    assert overlay_texts(job) == overlay_texts(make_job(charters, hook_text=None))
    assert overlay_digest(job) == overlay_digest(make_job(charters, hook_text=None))


def test_a_hook_is_kept_without_its_surrounding_blanks_only_in_the_text(
    charters: dict[str, BrandCharter], tmp_path: Path
) -> None:
    job = make_job(charters, hook_text="  Wait for it  ")
    assert "Wait for it" in overlay_texts(job)
    assert any(o.png.name == "hook.png" for o in BaseModule().plan(job, tmp_path).overlays)


# --- the source holds visible escapes, never invisible characters ------------------------------


def test_the_text_constants_are_the_expected_escaped_characters() -> None:
    from rushes.render import text as text_module

    assert text_module.ELLIPSIS == "\u2026"
    assert text_module._ZWJ == "\u200d"
    assert frozenset({"\ufe0e", "\ufe0f"}) == text_module._VARIATION_SELECTORS


def test_no_invisible_character_is_written_raw_in_the_source() -> None:
    """Format characters (ZWJ, variation selectors, BOM...) must be `\\u` escapes: an editor
    shows nothing for them, so a raw one cannot be reviewed."""
    import unicodedata

    root = REPO_CONFIG.parent
    offenders = []
    for path in [*(root / "src").rglob("*.py"), *(root / "tests").glob("*.py")]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for char in line:
                invisible = unicodedata.category(char) == "Cf" or "\ufe00" <= char <= "\ufe0f"
                if invisible:
                    offenders.append(f"{path.relative_to(root)}:{number} U+{ord(char):04X}")
    assert offenders == []
