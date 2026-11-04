"""Pure text helpers (no I/O)."""

import re
import unicodedata

_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_NOT_TAG = re.compile(r"[^a-z0-9_]")


def _fold(text: str) -> str:
    """Lowercase ASCII with accents folded (é → e)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def hashtag_from_name(name: str) -> str:
    """'Tom and Jerry' → '#tomandjerry': accents folded, only [a-z0-9] kept."""
    return "#" + _NOT_ALNUM.sub("", _fold(name))


def normalize_hashtag(tag: str) -> str:
    """'#TomAndJerry' → '#tomandjerry': lowercase, accents folded, '#' added if missing."""
    return "#" + _NOT_TAG.sub("", _fold(tag.strip().lstrip("#")))
