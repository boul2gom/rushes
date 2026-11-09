"""Captions (R12, R13): template, TikTok 50-150 characters, name within the visible start."""

from pathlib import Path

import pytest

from rushes.errors import RushesError
from rushes.meta.caption import (
    TEST_PREFIX,
    build_caption,
    caption_problems,
    part_label,
    pick_question,
    template_source,
)

SPEC_TAGS = ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstick"]
IG_TAGS = ["#cartoons", "#nostalgia", "#tomandjerry", "#slapstick"]
HOOK = "Tom really thought this plan would work 😭"


def caption(**overrides: object) -> str:
    args: dict[str, object] = {
        "platform": "tiktok",
        "language": "en",
        "hook": HOOK,
        "name": "Tom and Jerry",
        "part_number": 2,
        "situation": None,
        "question": "Who wins? 👇",
        "credit": None,
        "hashtags": SPEC_TAGS,
        "test": False,
    }
    if overrides.get("platform") == "instagram":
        args["hashtags"] = IG_TAGS  # no #fyp on Instagram (R13)
    args.update(overrides)
    return build_caption(**args)  # type: ignore[arg-type]


def test_caption_matches_spec_example() -> None:
    assert caption() == (
        "Tom really thought this plan would work 😭 Tom and Jerry, Part 2. Who wins? 👇 "
        "#fyp #nostalgiatok #cartoons #tomandjerry #slapstick"
    )


def test_caption_french_uses_partie() -> None:
    text = caption(language="fr", hook=None, question="Qui gagne ? 👇", name="Squeezie",
                   hashtags=["#fyp", "#squeezie"])  # fmt: skip
    assert text == "Squeezie, Partie 2. Qui gagne ? 👇 #fyp #squeezie"


def test_caption_without_part_number_has_no_label() -> None:
    text = caption(part_number=None, hashtags=["#fyp"])
    assert ", Part" not in text and "Jerry. Who" in text


def test_caption_situation_and_credit_slots() -> None:
    text = caption(situation="in the kitchen", credit="🎥 Squeezie", hashtags=["#a"])
    assert text == (
        "Tom really thought this plan would work 😭 Tom and Jerry, Part 2 in the kitchen. "
        "Who wins? 👇 🎥 Squeezie #a"
    )


def test_caption_length_50_150_tiktok() -> None:
    long_situation = "in the kitchen while the dog sleeps and the cat is plotting something big"
    text = caption(situation=long_situation)
    assert 50 <= len(text) <= 150
    assert long_situation not in text  # the situation went first
    assert "Who wins? 👇" in text  # the question is kept while it fits


def test_caption_drops_the_question_after_the_situation_when_still_too_long() -> None:
    tags = ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstickcomedy"]
    text = caption(hook="ab " * 20, hashtags=tags,
                   situation="in the kitchen")  # fmt: skip
    assert len(text) <= 150
    assert "Who wins" not in text and "kitchen" not in text
    assert "Tom and Jerry, Part 2." in text and text.endswith("#slapstickcomedy")
    assert "  " not in text


def test_caption_long_credit_is_never_dropped() -> None:
    credit = "🎥 " + "Very Long Creator Name " * 3
    text = caption(credit=credit.strip(), hook=None)
    assert credit.strip() in text


def test_r12_name_within_80_chars_tiktok() -> None:
    hook = "x" * 119 + "!"
    text = caption(hook=hook)
    assert hook not in text  # the hook is removed
    assert text.index("Tom and Jerry") + len("Tom and Jerry") <= 80
    assert caption_problems(text, platform="tiktok", name="Tom and Jerry") == []


def test_r12_hook_is_kept_when_the_name_still_fits_in_80() -> None:
    text = caption(hook="y" * 30)
    assert text.startswith("y" * 30 + " Tom and Jerry")


def test_r12_name_within_125_chars_instagram() -> None:
    hook = "word " * 24 + "end"  # 117 chars: the name would end after 125
    text = caption(platform="instagram", hook=hook)
    assert "word word" not in text
    assert text.index("Tom and Jerry") + len("Tom and Jerry") <= 125
    # a 100-char hook still lets the name end within 125 on Instagram (but not within 80)
    ok = caption(platform="instagram", hook="z" * 100)
    assert ok.startswith("z" * 100 + " Tom and Jerry")
    assert caption_problems(ok, platform="instagram", name="Tom and Jerry") == []
    assert caption_problems(ok, platform="tiktok", name="Tom and Jerry")  # too far for TikTok


def test_instagram_has_no_length_cap() -> None:
    text = caption(platform="instagram", situation="s " * 100)
    assert (
        len(text) > 150 and caption_problems(text, platform="instagram", name="Tom and Jerry") == []
    )


def test_test_post_caption_prefixed() -> None:
    text = caption(test=True)
    assert text.startswith("NE PAS PUBLIER — Tom really")
    assert text.startswith(TEST_PREFIX)
    assert text.removeprefix(TEST_PREFIX) == caption(test=False)


def test_test_prefix_is_not_counted_against_the_limits() -> None:
    assert len(caption(test=True)) > 150 - 5  # the prefix is there
    fits = caption(hook="y" * 60, test=True)
    body = fits.removeprefix(TEST_PREFIX)
    assert len(body) <= 150 and body.index("Tom and Jerry") + 13 <= 80
    assert caption_problems(fits, platform="tiktok", name="Tom and Jerry") == []


def test_caption_problems_are_consistent_with_build_caption() -> None:
    for platform in ("tiktok", "instagram"):
        for hook in (None, "a", "b " * 40):
            for situation in (None, "c " * 30):
                text = caption(platform=platform, hook=hook, situation=situation)
                assert caption_problems(text, platform=platform, name="Tom and Jerry") == [], text


def test_caption_problems_report_each_rule() -> None:
    name = "Tom and Jerry"
    assert any("R12" in p for p in caption_problems("x " * 50 + name, platform="tiktok", name=name))
    assert any(
        "introuvable" in p
        for p in caption_problems("Nothing here " * 5, platform="tiktok", name=name)
    )
    short = caption_problems("Tom and Jerry. #a", platform="tiktok", name=name)
    assert any("50" in p and "150" in p for p in short)
    big = caption_problems(f"{name} " + "z" * 150, platform="tiktok", name=name)
    assert any("150" in p for p in big)


def test_r13_caption_problems_flag_hashtags() -> None:
    name = "Tom and Jerry"
    base = f"{name}. Who wins? 👇 "
    six = caption_problems(base + "#a #b #c #d #e #f", platform="instagram", name=name)
    assert any("R13" in p and "5" in p for p in six)
    assert any(
        "#viral" in p for p in caption_problems(base + "#viral #a", platform="instagram", name=name)
    )
    assert any(
        "#foryou" in p for p in caption_problems(base + "#ForYou", platform="tiktok", name=name)
    )
    assert any(
        "#fyp" in p for p in caption_problems(base + "#fyp", platform="instagram", name=name)
    )
    assert not any(
        "#fyp" in p
        for p in caption_problems(
            base + "#fyp #cartoons #nostalgiatok", platform="tiktok", name=name
        )
    )


def test_r9_caption_never_shows_the_total_of_parts() -> None:
    name = "Tom and Jerry"
    for bad in ("Part 2/5", "Part 2 of 5", "Partie 2 sur 5"):
        text = f"{name}, {bad}. Who wins? 👇 #fyp #nostalgiatok #cartoons #a"
        assert any("R9" in p for p in caption_problems(text, platform="tiktok", name=name)), bad
    good = f"{name}, Part 2. Who wins? 👇 #fyp #nostalgiatok #cartoons #a"
    assert caption_problems(good, platform="tiktok", name=name) == []


def test_caption_collapses_blank_slots_into_single_spaces() -> None:
    text = caption(hook=None, question="")
    assert "  " not in text and text.startswith("Tom and Jerry, Part 2. #fyp")


def test_caption_normalizes_whitespace_of_free_text() -> None:
    text = caption(hook="Wait\n  for   it", hashtags=["#a"])
    assert text.startswith("Wait for it Tom and Jerry")


def test_caption_with_unicode_name_and_emoji() -> None:
    text = caption(name="Éléonore 🐭", hook=None, hashtags=["#a"])
    assert text.startswith("Éléonore 🐭, Part 2.")
    assert caption_problems(text, platform="instagram", name="Éléonore 🐭") == []


def test_caption_unknown_language_without_template_is_refused() -> None:
    with pytest.raises(RushesError, match="langue"):
        caption(language="de")


def test_caption_custom_template_replaces_the_default() -> None:
    text = caption(template="{{ name }} // {{ part_label }} // {{ hashtags }}", hook=None)
    assert text == "Tom and Jerry // Part 2 // #fyp #nostalgiatok #cartoons #tomandjerry #slapstick"


def test_caption_template_cannot_use_unknown_variables_or_unsafe_code() -> None:
    with pytest.raises(RushesError, match="gabarit"):
        caption(template="{{ nope }}")
    with pytest.raises(RushesError, match="gabarit"):
        caption(template="{{ ''.__class__.__mro__ }}")
    with pytest.raises(RushesError, match="gabarit"):
        caption(template="{% if %}")


def test_questions_are_drawn_with_a_seed_in_the_language() -> None:
    en = {pick_question("en", seed) for seed in range(40)}
    assert en == {"Who wins? 👇", "Did you see it coming? 👀", "Best moment? 👇"}
    fr = {pick_question("fr", seed) for seed in range(40)}
    assert fr == {"Qui gagne ? 👇", "Tu l'avais vu venir ? 👀", "Le meilleur moment ? 👇"}
    assert pick_question("en", 3) == pick_question("en", 3)
    with pytest.raises(RushesError, match="langue"):
        pick_question("de", 1)


def test_part_label_by_language() -> None:
    assert part_label("en", 3) == "Part 3" and part_label("fr", 3) == "Partie 3"
    assert part_label("en", None) is None


def test_template_source_prefers_the_subject_override(tmp_path: Path) -> None:
    default = template_source("en", tmp_path, "tom_and_jerry")
    assert "{{ name }}" in default
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "tom_and_jerry.j2").write_text("custom {{ name }}", encoding="utf-8")
    assert template_source("en", tmp_path, "tom_and_jerry") == "custom {{ name }}"
    assert template_source("en", tmp_path, "oggy") == default


def test_template_source_ignores_ids_that_could_leave_the_folder(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    (tmp_path / "evil.j2").write_text("EVIL", encoding="utf-8")
    assert template_source("en", tmp_path, "../evil") != "EVIL"
