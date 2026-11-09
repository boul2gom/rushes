"""Hashtags of a post (R13): required ones first, then a seeded draw from the pool, 5 at most."""

import random
from collections.abc import Mapping, Sequence

from rushes.config import FORBIDDEN_HASHTAGS, MAX_HASHTAGS, Platform, Vertical
from rushes.core.text import hashtag_from_name, normalize_hashtag

NOT_ON_INSTAGRAM = frozenset({"#fyp"})


def _clean(tags: Sequence[str], platform: Platform) -> list[str]:
    """Normalized, without the empty or forbidden ones and without duplicates (first kept)."""
    banned = FORBIDDEN_HASHTAGS | (NOT_ON_INSTAGRAM if platform == "instagram" else frozenset())
    kept: list[str] = []
    for tag in tags:
        normal = normalize_hashtag(tag)
        if normal != "#" and normal not in banned and normal not in kept:
            kept.append(normal)
    return kept


def resolve_hashtags(
    *,
    required: Sequence[str],
    tokens: Mapping[str, str],
    pool: Sequence[str],
    platform: Platform,
    seed: int,
    max_total: int = MAX_HASHTAGS,
) -> list[str]:
    """`required` (with `{series}`-like tokens replaced), then pool tags drawn with `seed`.

    ValueError for a token missing from `tokens`. Never more than `max_total` (and never more
    than 5, R13) tags, so a long `required` list is cut at the end."""
    expanded: list[str] = []
    for tag in required:
        if tag.startswith("{"):
            if tag not in tokens:
                raise ValueError(f"jeton sans valeur : {tag}")
            expanded.append(tokens[tag])
        else:
            expanded.append(tag)
    limit = min(max_total, MAX_HASHTAGS)
    tags = _clean(expanded, platform)[:limit]
    drawn = [tag for tag in _clean(pool, platform) if tag not in tags]
    random.Random(seed).shuffle(drawn)
    return [*tags, *drawn[: max(limit - len(tags), 0)]]


def default_pool(name: str, vertical: Vertical) -> list[str]:
    """Pool used until the market study has written one (autonomy: seeded from the name)."""
    tag = hashtag_from_name(name)
    named = [] if tag == "#" else [f"{tag}{suffix}" for suffix in _SUFFIXES[vertical]]
    return [*named, *_FIXED[vertical]]


# cartoons: 3 tags; creators: up to 4 rotating ones on Instagram (R13)
_SUFFIXES: dict[Vertical, tuple[str, ...]] = {
    "cartoons": ("clips", "cartoon"),
    "creators": ("clips", "bestof", "moments", "fan"),
}
_FIXED: dict[Vertical, tuple[str, ...]] = {"cartoons": ("#classiccartoons",), "creators": ()}
