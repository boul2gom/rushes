"""Zero-shot tags and character presence from CLIP vectors (task 12a).

Every vector is L2-normalised, so a dot product is a cosine.
"""

from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

TAG_PROMPTS: Mapping[str, str] = {
    "chase": "a cartoon character chasing another one",
    "fall": "a cartoon character falling down",
    "explosion": "a cartoon explosion",
    "trap": "a cartoon character caught in a trap",
    "hit": "a cartoon character getting hit on the head",
    "food": "a cartoon character eating food",
    "sleep": "a cartoon character sleeping",
    "scared": "a scared cartoon character",
    "angry": "an angry cartoon character",
    "music": "a cartoon character playing music",
}


def zero_shot_tags(
    img: NDArray[np.float32],
    txt: NDArray[np.float32],
    labels: Sequence[str],
    *,
    min_cos: float,
    top: int,
) -> list[list[str]]:
    """Per image, its `top` best labels whose cosine reaches `min_cos`, best first.

    `txt[i]` is the vector of `labels[i]`. Equal cosines keep the order of `labels`.
    """
    cosines = img @ txt.T
    found: list[list[str]] = []
    for row in cosines:
        best = sorted(range(len(labels)), key=lambda i: (-float(row[i]), i))
        found.append([labels[i] for i in best[:top] if row[i] >= min_cos])
    return found


def character_presence(
    img: NDArray[np.float32], refs: Mapping[str, NDArray[np.float32]], *, min_cos: float
) -> list[list[str]]:
    """Per image, the names (sorted) of the characters whose reference vector is close enough."""
    names = sorted(refs)
    if not names:
        return [[] for _ in range(len(img))]
    cosines = img @ np.stack([refs[name] for name in names]).T
    return [
        [name for name, cos in zip(names, row, strict=True) if cos >= min_cos] for row in cosines
    ]
