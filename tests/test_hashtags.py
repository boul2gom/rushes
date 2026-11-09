"""Hashtags (R13): required first, then the pool drawn with a seed, 5 at most."""

import pytest

from rushes.meta.hashtags import default_pool, resolve_hashtags

CARTOONS_TT = ["#fyp", "#nostalgiatok", "#cartoons", "{series}"]


def test_r13_required_hashtags_tiktok_cartoons() -> None:
    tags = resolve_hashtags(
        required=["#fyp", "#nostalgiatok", "#cartoons", "{series}"],
        tokens={"{series}": "#tomandjerry"},
        pool=["#slapstick"],
        platform="tiktok",
        seed=1,
    )
    assert tags == ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstick"]


def test_r13_instagram_max_5_no_fyp() -> None:
    tags = resolve_hashtags(
        required=["#fyp", "#cartoons", "{nostalgia}"],
        tokens={"{nostalgia}": "#nostalgia"},
        pool=["#a", "#b", "#c", "#d", "#e", "#f"],
        platform="instagram",
        seed=7,
    )
    assert len(tags) == 5
    assert "#fyp" not in tags
    assert tags[:2] == ["#cartoons", "#nostalgia"]  # required first, in order
    assert set(tags[2:]) <= {"#a", "#b", "#c", "#d", "#e", "#f"}


def test_r13_never_viral_foryou() -> None:
    tags = resolve_hashtags(
        required=["#fyp"], tokens={}, pool=["#viral", "#foryou", "#x"], platform="tiktok", seed=3
    )
    assert tags == ["#fyp", "#x"]


def test_r13_forbidden_tags_are_removed_from_required_too() -> None:
    tags = resolve_hashtags(
        required=["#viral", "#ForYou", "#cartoons"], tokens={}, pool=[], platform="tiktok", seed=1
    )
    assert tags == ["#cartoons"]


def test_r13_never_more_than_five_even_when_required_has_more() -> None:
    required = [f"#r{i}" for i in range(8)]
    tags = resolve_hashtags(required=required, tokens={}, pool=["#p"], platform="tiktok", seed=1)
    assert tags == required[:5]


def test_r13_max_total_below_five_is_honoured() -> None:
    tags = resolve_hashtags(
        required=["#a", "#b"], tokens={}, pool=["#c", "#d"], platform="tiktok", seed=1, max_total=3
    )
    assert len(tags) == 3 and tags[:2] == ["#a", "#b"]


def test_r13_duplicates_are_removed_case_insensitively_keeping_the_first() -> None:
    tags = resolve_hashtags(
        required=["#Cartoons", "{series}"],
        tokens={"{series}": "#CARTOONS"},
        pool=["#cartoons", "#TomAndJerry", "#tomandjerry", "#x"],
        platform="tiktok",
        seed=1,
    )
    assert tags[0] == "#cartoons" and sorted(tags[1:]) == ["#tomandjerry", "#x"]


def test_r13_pool_draw_is_seeded_and_reproducible() -> None:
    pool = [f"#p{i}" for i in range(20)]

    def draw(seed: int) -> list[str]:
        return resolve_hashtags(
            required=["#a"], tokens={}, pool=pool, platform="instagram", seed=seed
        )

    assert draw(5) == draw(5)
    assert len({tuple(draw(s)) for s in range(30)}) > 1  # the seed matters


def test_r13_empty_pool_gives_only_the_required() -> None:
    tags = resolve_hashtags(
        required=["#fyp", "{creator}"],
        tokens={"{creator}": "#squeezie"},
        pool=[],
        platform="tiktok",
        seed=1,
    )
    assert tags == ["#fyp", "#squeezie"]


def test_r13_pool_smaller_than_the_room_is_used_entirely() -> None:
    tags = resolve_hashtags(
        required=["#a"], tokens={}, pool=["#b", "#c"], platform="tiktok", seed=1
    )
    assert sorted(tags[1:]) == ["#b", "#c"]


def test_r13_unknown_token_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"\{series\}"):
        resolve_hashtags(required=["{series}"], tokens={}, pool=[], platform="tiktok", seed=1)


def test_r13_pool_entries_are_normalized_and_empty_ones_dropped() -> None:
    tags = resolve_hashtags(
        required=[], tokens={}, pool=["Slap Stick", "#É-té", "😀", "", "#"], platform="tiktok",
        seed=1, max_total=5,
    )  # fmt: skip
    assert sorted(tags) == ["#ete", "#slapstick"]


def test_r13_token_value_without_letters_is_dropped() -> None:
    tags = resolve_hashtags(
        required=["{series}", "#a"], tokens={"{series}": "#"}, pool=[], platform="tiktok", seed=1
    )
    assert tags == ["#a"]


def test_r13_fyp_stays_on_tiktok() -> None:
    tags = resolve_hashtags(required=CARTOONS_TT[:1], tokens={}, pool=[], platform="tiktok", seed=1)
    assert tags == ["#fyp"]


def test_r13_fyp_in_the_pool_is_removed_on_instagram_only() -> None:
    kwargs = {"required": ["#a"], "tokens": {}, "pool": ["#fyp"], "seed": 1}
    assert resolve_hashtags(platform="instagram", **kwargs) == ["#a"]  # type: ignore[arg-type]
    assert resolve_hashtags(platform="tiktok", **kwargs) == ["#a", "#fyp"]  # type: ignore[arg-type]


def test_default_pool_cartoons() -> None:
    assert default_pool("Tom and Jerry", "cartoons") == [
        "#tomandjerryclips",
        "#tomandjerrycartoon",
        "#classiccartoons",
    ]


def test_default_pool_creators_has_four_for_instagram() -> None:
    assert default_pool("Squeezie", "creators") == [
        "#squeezieclips",
        "#squeeziebestof",
        "#squeeziemoments",
        "#squeeziefan",
    ]


def test_default_pool_accented_and_emoji_names_still_give_valid_tags() -> None:
    pool = default_pool("Les Aventures d'Éléonore", "cartoons")
    assert pool[0] == "#lesaventuresdeleonoreclips"
    assert all(t.startswith("#") and t == t.lower() for t in pool)
    assert default_pool("😀😀", "cartoons") == ["#classiccartoons"]  # nothing left to prefix
