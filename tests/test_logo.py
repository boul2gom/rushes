"""Burned-in logo detection (R34, task 11)."""

from pathlib import Path
from typing import get_args

import pytest

from fixtures.generator import Synth, SynthSpec
from rushes.clean.logo import CORNERS, Corner, detect_logo
from rushes.media.ffmpeg import run_ffmpeg

SMALL = (320, 180)
SHOTS = [2500, 5000, 7500]
KWARGS = {"samples": 120, "static_std": 6.0, "min_edge_ratio": 0.01}


def _spec(**changes: object) -> SynthSpec:
    base = {"duration_ms": 10_000, "size": SMALL, "fps": 10, "shots_ms": SHOTS}
    return SynthSpec(**{**base, **changes})  # type: ignore[arg-type]


def test_corners_are_the_four_literals() -> None:
    assert set(CORNERS) == set(get_args(Corner.__value__))


@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_logo_is_found_in_each_corner(synth: Synth, corner: Corner) -> None:
    assert detect_logo(synth(_spec(logo_corner=corner)), **KWARGS) == corner


def test_no_logo_on_a_clean_video(synth: Synth) -> None:
    assert detect_logo(synth(_spec()), **KWARGS) is None


def test_static_slideshow_is_not_a_logo(synth: Synth) -> None:
    # one picture, nothing moves in the centre: even a corner box says nothing
    still = SynthSpec(duration_ms=10_000, size=SMALL, fps=10, logo_corner="tr")
    assert detect_logo(synth(still), **KWARGS) is None


def test_logo_on_a_larger_picture(synth: Synth) -> None:
    spec = SynthSpec(
        duration_ms=8000, size=(854, 480), fps=5, shots_ms=[2000, 4000, 6000], logo_corner="bl"
    )
    assert detect_logo(synth(spec), **KWARGS) == "bl"


def test_a_higher_edge_ratio_than_the_logo_gives_none(synth: Synth) -> None:
    video = synth(_spec(logo_corner="tl"))
    assert detect_logo(video, samples=120, static_std=6.0, min_edge_ratio=0.9) is None


def test_too_few_samples_for_a_verdict_gives_none(synth: Synth) -> None:
    video = synth(_spec(logo_corner="tl"))
    assert detect_logo(video, samples=3, static_std=6.0, min_edge_ratio=0.01) is None


# --- black bars around the picture (R34: a bar edge is not a logo) ---------------------------


def _padded(
    video: Path, out: Path, *, size: tuple[int, int], at: tuple[int, int], color: str
) -> Path:
    """The video on a `size` canvas of `color`: bars on the sides and/or at the top and bottom."""
    pad = f"pad={size[0]}:{size[1]}:{at[0]}:{at[1]}:color={color}"
    run_ffmpeg(["-i", video, "-vf", pad, "-c:v", "libx264", "-preset", "ultrafast", "-an", out])
    return out


def _filtered(video: Path, out: Path, vf: str) -> Path:
    """The video through an ffmpeg video filter (drawbox, geq, ...)."""
    run_ffmpeg(["-i", video, "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-an", out])
    return out


def _picture(synth: Synth, size: tuple[int, int], **changes: object) -> Path:
    return synth(_spec(size=size, **changes))


def test_r34_pillarboxed_clean_video_has_no_logo(synth: Synth, tmp_path: Path) -> None:
    # a 4:3 picture on a 16:9 canvas: the bar/picture edge is static and sharp, but not a logo
    video = _padded(
        _picture(synth, (240, 180)), tmp_path / "p.mp4", size=SMALL, at=(40, 0), color="black"
    )
    assert detect_logo(video, **KWARGS) is None


def test_r34_letterboxed_clean_video_has_no_logo(synth: Synth, tmp_path: Path) -> None:
    video = _padded(
        _picture(synth, (320, 136)), tmp_path / "l.mp4", size=SMALL, at=(0, 22), color="black"
    )
    assert detect_logo(video, **KWARGS) is None


def test_r34_bars_on_all_four_sides_have_no_logo(synth: Synth, tmp_path: Path) -> None:
    video = _padded(
        _picture(synth, (240, 136)), tmp_path / "w.mp4", size=SMALL, at=(40, 22), color="black"
    )
    assert detect_logo(video, **KWARGS) is None


def test_r34_dark_gray_bars_are_tolerated(synth: Synth, tmp_path: Path) -> None:
    video = _padded(
        _picture(synth, (240, 180)), tmp_path / "g.mp4", size=SMALL, at=(40, 0), color="0x141414"
    )
    assert detect_logo(video, **KWARGS) is None


@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_r34_logo_inside_a_pillarboxed_picture_is_found(
    synth: Synth, tmp_path: Path, corner: Corner
) -> None:
    # the logo sits in the corner of the picture, not of the canvas
    picture = _picture(synth, (240, 180), logo_corner=corner)
    video = _padded(picture, tmp_path / "pl.mp4", size=SMALL, at=(40, 0), color="black")
    assert detect_logo(video, **KWARGS) == corner


def test_r34_logo_inside_a_letterboxed_picture_is_found(synth: Synth, tmp_path: Path) -> None:
    picture = _picture(synth, (320, 136), logo_corner="tr")
    video = _padded(picture, tmp_path / "ll.mp4", size=SMALL, at=(0, 22), color="black")
    assert detect_logo(video, **KWARGS) == "tr"


def test_r34_a_picture_that_is_all_black_has_no_logo(synth: Synth) -> None:
    assert detect_logo(synth(_spec(black_ms=[(0, 10_000)])), **KWARGS) is None


def test_r34_static_slideshow_with_bars_is_still_not_a_logo(synth: Synth, tmp_path: Path) -> None:
    still = SynthSpec(duration_ms=10_000, size=(240, 180), fps=10, logo_corner="tr")
    video = _padded(synth(still), tmp_path / "s.mp4", size=SMALL, at=(40, 0), color="black")
    assert detect_logo(video, **KWARGS) is None


def test_r34_dark_gray_bars_above_the_old_black_level_are_bars_too(
    synth: Synth, tmp_path: Path
) -> None:
    video = _padded(
        _picture(synth, (240, 180)), tmp_path / "g3.mp4", size=SMALL, at=(40, 0), color="0x303030"
    )
    assert detect_logo(video, **KWARGS) is None


@pytest.mark.parametrize("color", ["0x808080", "0xc8c8c8", "0x2060a0"])
def test_r34_lighter_flat_colour_bars_are_bars(synth: Synth, tmp_path: Path, color: str) -> None:
    video = _padded(
        _picture(synth, (240, 180)), tmp_path / "f.mp4", size=SMALL, at=(40, 0), color=color
    )
    assert detect_logo(video, **KWARGS) is None


def test_r34_logo_inside_a_picture_with_gray_bars_is_found(synth: Synth, tmp_path: Path) -> None:
    picture = _picture(synth, (240, 180), logo_corner="tr")
    video = _padded(picture, tmp_path / "gl.mp4", size=SMALL, at=(40, 0), color="0x303030")
    assert detect_logo(video, **KWARGS) == "tr"


def test_r34_thin_static_frame_line_is_not_a_logo(synth: Synth, tmp_path: Path) -> None:
    # the same static outline in all four corners: no corner dominates
    frame = "drawbox=x=8:y=8:w=iw-16:h=ih-16:color=white:t=2"
    video = _filtered(_picture(synth, SMALL), tmp_path / "frame.mp4", frame)
    assert detect_logo(video, **KWARGS) is None


def test_r34_rounded_film_gate_corners_are_not_a_logo(synth: Synth, tmp_path: Path) -> None:
    x, y = "min(X,W-1-X)", "min(Y,H-1-Y)"
    gate = (
        f"geq=lum='if(lt({x},20)*lt({y},20)*gt(hypot(20-{x},20-{y}),20),0,lum(X,Y))'"
        ":cb='cb(X,Y)':cr='cr(X,Y)'"
    )
    video = _filtered(_picture(synth, SMALL), tmp_path / "gate.mp4", gate)
    assert detect_logo(video, **KWARGS) is None


def test_r34_a_logo_stays_found_with_rounded_film_gate_corners(
    synth: Synth, tmp_path: Path
) -> None:
    x, y = "min(X,W-1-X)", "min(Y,H-1-Y)"
    gate = (
        f"geq=lum='if(lt({x},20)*lt({y},20)*gt(hypot(20-{x},20-{y}),20),0,lum(X,Y))'"
        ":cb='cb(X,Y)':cr='cr(X,Y)'"
    )
    video = _filtered(_picture(synth, SMALL, logo_corner="tr"), tmp_path / "gl2.mp4", gate)
    assert detect_logo(video, **KWARGS) == "tr"  # 3.5 times the other corners


def _logo_from(synth: Synth, tmp_path: Path, seconds: float, corner: str = "tr") -> Path:
    """A clean video where a logo box shows from `seconds` on (a logo missing at the start)."""
    side = SMALL[1] // 8
    x = "0" if corner[1] == "l" else f"iw-{side}"
    box = f"drawbox=x={x}:y=0:w={side}:h={side}:color=white:t=fill:enable='gte(t,{seconds})'"
    return _filtered(_picture(synth, SMALL), tmp_path / f"late{seconds}.mp4", box)


def test_r34_logo_missing_during_the_first_5_percent_is_still_found(
    synth: Synth, tmp_path: Path
) -> None:
    assert detect_logo(_logo_from(synth, tmp_path, 0.5), **KWARGS) == "tr"


def test_r34_logo_missing_for_a_fifth_of_the_video_is_not_static(
    synth: Synth, tmp_path: Path
) -> None:
    assert detect_logo(_logo_from(synth, tmp_path, 2.0), **KWARGS) is None


def test_r34_static_share_is_the_tolerance_for_a_missing_logo(synth: Synth, tmp_path: Path) -> None:
    late = _logo_from(synth, tmp_path, 2.0)
    loose = {**KWARGS, "static_share": 0.75}
    assert detect_logo(late, **loose) == "tr"
