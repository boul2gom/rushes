"""Burned-in logo (R34): a corner whose pixels stay put and draw an outline.

A pixel is static when most samples (`static_share`) lie within +/- `static_std` of its temporal
median, so a logo that is missing from a few samples (credits, a bumper) is still seen. The corner
of the active picture (bars cut off) with the largest share wins if it reaches `min_edge_ratio` and
dominates the others: a static structure in all four corners (a frame line, rounded film-gate
corners) is not a logo. Known limits: a semi-transparent logo (about 50 % opacity) blends with the
moving picture, has no stable outline and is not detected; a logo in a picture that also has a
static frame line around it may not dominate the frame's own corners.
"""

from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from rushes.media.frames import sample_gray
from rushes.media.probe import probe

type Corner = Literal["tl", "tr", "bl", "br"]

CORNERS: tuple[Corner, ...] = ("tl", "tr", "bl", "br")
SAMPLE_WIDTH = 320  # px; small enough to be fast, large enough for a logo to keep its outline
CORNER_SHARE = 0.2  # a corner is 20 % x 20 % of the picture
MIN_FRAMES = 8
CENTER_MIN_STD = 10.0  # below it the picture hardly moves at all (a slideshow): no verdict
CANNY = (50, 150)
BAR_PTP = 8  # a bar is flat: its levels span at most this (0-255) over all samples and pixels
MIN_BAR_PX = 3  # a thinner flat border is part of the picture
LOGO_DOMINANCE = 2.0  # the winning corner's share is at least this times the runner-up's
BAR_MARGIN_PX = 2  # taken off the picture next to a bar: codec ringing blurs the bar's edge
MIN_ACTIVE_PX = 16  # a picture smaller than this (either way) says nothing


def _corner_slices(height: int, width: int) -> dict[Corner, tuple[slice, slice]]:
    h, w = max(1, round(height * CORNER_SHARE)), max(1, round(width * CORNER_SHARE))
    top, bottom, left, right = (
        slice(0, h),
        slice(height - h, height),
        slice(0, w),
        slice(width - w, width),
    )
    return {"tl": (top, left), "tr": (top, right), "bl": (bottom, left), "br": (bottom, right)}


def _flat_run(flat: NDArray[np.bool_]) -> tuple[int, int]:
    """(leading, trailing) lengths of the flat run at both ends of a line of columns or rows."""
    varied = np.flatnonzero(~flat)
    if varied.size == 0:
        return flat.size, flat.size
    return int(varied[0]), int(flat.size - 1 - varied[-1])


def _active_area(frames: NDArray[np.float32]) -> NDArray[np.float32] | None:
    """The samples cropped to the picture, without pillar/letter-box bars; None if nothing is left.

    A bar is a run of border columns (or rows), scanned inward from each border, that keep one
    flat static colour whatever its level: their levels span at most BAR_PTP over every sample and
    along their whole length. It needs MIN_BAR_PX to count. A video without bars comes back
    unchanged.
    """
    _, height, width = frames.shape
    columns = frames.max(axis=(0, 1)) - frames.min(axis=(0, 1))
    rows = frames.max(axis=(0, 2)) - frames.min(axis=(0, 2))
    left, right = _flat_run(columns <= BAR_PTP)
    top, bottom = _flat_run(rows <= BAR_PTP)

    def inset(bar: int) -> int:
        return bar + BAR_MARGIN_PX if bar >= MIN_BAR_PX else 0

    x0, x1 = inset(left), width - inset(right)
    y0, y1 = inset(top), height - inset(bottom)
    if x1 - x0 < MIN_ACTIVE_PX or y1 - y0 < MIN_ACTIVE_PX:
        return None
    return frames[:, y0:y1, x0:x1]


def _logo_shares(
    frames: NDArray[np.float32], static_std: float, static_share: float
) -> tuple[dict[Corner, float], float]:
    """Per corner, the share of its pixels that are static and on an outline; the centre's std."""
    _, height, width = frames.shape
    std = frames.std(axis=0)
    centered = np.median(frames, axis=0)
    steady = (np.abs(frames - centered) <= static_std).mean(axis=0) >= static_share
    median = centered.astype(np.uint8)
    outline = cv2.dilate(cv2.Canny(median, *CANNY), np.ones((3, 3), np.uint8)) > 0  # +1 px
    both = steady & outline
    shares = {
        name: float(both[rows, cols].mean())
        for name, (rows, cols) in _corner_slices(height, width).items()
    }
    center = std[height // 4 : height - height // 4, width // 4 : width - width // 4]
    return shares, float(center.mean())


def detect_logo(
    video: Path,
    *,
    samples: int,
    static_std: float,
    min_edge_ratio: float,
    static_share: float = 0.9,
    tmp_dir: Path | None = None,
) -> Corner | None:
    """The corner holding a burned-in logo, or None.

    `samples` gray images spread over the video: a corner counts through the share of its pixels
    that stay put (at least `static_share` of the samples within +/- `static_std` of the temporal
    median) and lie on an outline of the median picture. The corner with the largest share wins
    when it reaches `min_edge_ratio` and is at least LOGO_DOMINANCE times the second. A picture that
    hardly moves anywhere (centre std not above 10) says nothing: it is a still image, not a logo.
    Black pillar/letter-box bars are cut off first: the corners and the centre are those of the
    active picture, so the edge of a bar is never taken for a logo.
    """
    duration_s = probe(video).duration_ms / 1000
    if duration_s <= 0 or samples < 1:
        return None
    images = [
        img
        for _, img in sample_gray(
            video, fps=samples / duration_s, width=SAMPLE_WIDTH, tmp_dir=tmp_dir
        )
    ]
    if len(images) < MIN_FRAMES:
        return None
    picture = _active_area(np.stack(images).astype(np.float32))
    if picture is None:
        return None
    shares, center_std = _logo_shares(picture, static_std, static_share)
    if center_std <= CENTER_MIN_STD:
        return None
    ranked = sorted(CORNERS, key=lambda name: (-shares[name], CORNERS.index(name)))
    best, second = shares[ranked[0]], shares[ranked[1]]
    if best < min_edge_ratio or best < LOGO_DOMINANCE * second:
        return None
    return ranked[0]
