"""Render building blocks that need no video: canvas geometry, filter strings, loudnorm, args."""

import math
import re

import pytest

from rushes.config import RenderSettings
from rushes.errors import MediaError
from rushes.media import verify
from rushes.render.canvas import (
    CANVAS_H,
    CANVAS_W,
    VIDEO_TOP,
    Rect,
    plate_filter,
    video_rect,
)
from rushes.render.encode import encode_args
from rushes.render.loudnorm import (
    LoudnormMeasure,
    PlateReport,
    parse_measure,
    parse_report,
    second_pass_filter,
)

_LAYOUTS = ("square", "portrait", "vertical", "full")


def _chain(graph: str, start: str, end: str) -> str:
    """The filters between the `[start]` and `[end]` labels of a graph."""
    match = re.search(rf";\[{start}\](.*?)\[{end}\]", graph)
    assert match, graph
    return match.group(1)


def test_video_rect_per_layout() -> None:
    assert video_rect("square", 1280, 720) == Rect(0, 420, 1080, 1080)
    assert video_rect("portrait", 1280, 720) == Rect(0, 420, 1080, 1440)
    assert video_rect("vertical", 1280, 720) == Rect(0, 0, 1080, 1920)
    assert video_rect("full", 1440, 1080) == Rect(0, 420, 1080, 810)  # 4:3
    assert video_rect("full", 1080, 1920) == Rect(253, 420, 574, 1020)  # 9:16 is capped in height
    assert video_rect("full", 1920, 1080) == Rect(0, 420, 1080, 608)  # 16:9
    assert (CANVAS_W, CANVAS_H, VIDEO_TOP) == (1080, 1920, 420)


@pytest.mark.parametrize(
    ("width", "height"),
    [(1001, 563), (999, 777), (641, 1139), (1921, 1081), (4000, 100), (100, 4000), (3, 3), (2, 1)],
)
def test_video_rect_full_never_produces_an_odd_or_oversized_rect(width: int, height: int) -> None:
    rect = video_rect("full", width, height)
    assert rect.w % 2 == 0 and rect.h % 2 == 0
    assert 2 <= rect.w <= CANVAS_W and 2 <= rect.h <= 1020
    assert rect.y == VIDEO_TOP
    assert rect.x == (CANVAS_W - rect.w) // 2 and rect.x >= 0
    # the picture is scaled, not cropped: the shape is kept to the pixel
    assert abs(rect.w - rect.h * width / height) <= 1 or abs(rect.h - 1080 * height / width) <= 1


def test_video_rect_refuses_an_empty_picture() -> None:
    with pytest.raises(ValueError):
        video_rect("full", 0, 720)


def test_full_layout_keeps_whole_image() -> None:
    graph = plate_filter("full", 1440, 1080)
    foreground = _chain(graph, "fgin", "fg")
    assert "crop=" not in foreground  # nothing of the picture is lost
    assert "scale=1080:810" in foreground
    assert "overlay=0:420" in graph
    assert "crop=270:480" in graph  # the only crop is the blurred background's


def test_plate_filter_background_is_a_blurred_quarter_resolution_copy() -> None:
    background = _chain(plate_filter("square", 1280, 720), "bgin", "bg")
    assert background == (
        "scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
        "scale=1080:1920,setsar=1"
    )


@pytest.mark.parametrize("layout", ["square", "portrait", "full"])
def test_plate_filter_overlays_the_foreground_on_its_rect(layout: str) -> None:
    rect = video_rect(layout, 1280, 720)  # type: ignore[arg-type]
    graph = plate_filter(layout, 1280, 720)  # type: ignore[arg-type]
    assert f"overlay={rect.x}:{rect.y}" in graph
    assert f"scale={rect.w}:{rect.h}" in _chain(graph, "fgin", "fg")
    assert graph.endswith("[v]")


def test_plate_filter_vertical_fills_the_canvas_without_background() -> None:
    graph = plate_filter("vertical", 1280, 720)
    assert "boxblur" not in graph and "overlay" not in graph
    assert "scale=1080:1920" in graph and graph.endswith("[v]")


def test_plate_filter_square_crops_the_centre_then_scales() -> None:
    foreground = _chain(plate_filter("square", 1280, 720), "fgin", "fg")
    assert foreground == "crop=min(iw\\,ih):min(iw\\,ih),scale=1080:1080,setsar=1"


def test_plate_filter_crop_x_places_the_window_and_escapes_commas() -> None:
    graph = plate_filter("square", 1280, 720, crop_x="min(max(0,t*50),iw-ow)")
    foreground = _chain(graph, "fgin", "fg")
    assert foreground.startswith("crop=min(iw\\,ih):min(iw\\,ih):min(max(0\\,t*50)\\,iw-ow):")
    assert "(ih-oh)/2" in foreground  # still centred vertically


@pytest.mark.parametrize("bad", ["a:b", "x;y", "[0:v]", "'q'", "a\nb", "a\\b"])
def test_plate_filter_refuses_a_crop_x_that_could_break_the_graph(bad: str) -> None:
    with pytest.raises(ValueError, match="crop_x"):
        plate_filter("square", 1280, 720, crop_x=bad)


@pytest.mark.parametrize("layout", ["portrait", "vertical"])
def test_plate_filter_portrait_and_vertical_crop_to_their_ratio(layout: str) -> None:
    graph = plate_filter(layout, 1280, 720)  # type: ignore[arg-type]
    ratio = "ih*3/4" if layout == "portrait" else "ih*9/16"
    assert f"crop=min(iw\\,{ratio}):min(ih\\," in graph


@pytest.mark.parametrize("layout", _LAYOUTS)
def test_plate_filter_converts_pixel_aspect_before_anything(layout: str) -> None:
    graph = plate_filter(layout, 1920, 1080)  # type: ignore[arg-type]
    assert graph.startswith("[0:v]scale=trunc(iw*sar/2)*2:ih,setsar=1")
    assert graph.count("setsar=1") >= 2  # output pixels are square too


# --- loudnorm -----------------------------------------------------------------------------------

_JSON = """
[Parsed_loudnorm_0 @ 0x600001234]
{
\t"input_i" : "-30.12",
\t"input_tp" : "-18.30",
\t"input_lra" : "2.40",
\t"input_thresh" : "-40.71",
\t"output_i" : "-14.50",
\t"output_tp" : "-3.00",
\t"output_lra" : "1.50",
\t"output_thresh" : "-24.80",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.55"
}
"""


def test_r7_parse_measure_reads_the_json_block_of_the_log() -> None:
    log = "Stream mapping:\n" + _JSON + "\nsize=N/A time=00:00:04.00"
    assert parse_measure(log) == LoudnormMeasure(-30.12, -18.30, 2.40, -40.71, 0.55)


def test_r7_parse_measure_without_a_block_is_a_media_error() -> None:
    with pytest.raises(MediaError):
        parse_measure("rien d'utile ici")


def test_r7_second_pass_filter_is_linear_with_the_measured_values() -> None:
    measure = LoudnormMeasure(-30.12, -18.3, 2.4, -40.71, 0.55)
    assert second_pass_filter(measure) == (
        "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-30.12:measured_TP=-18.30:measured_LRA=2.40:"
        "measured_thresh=-40.71:offset=0.55:linear=true,aresample=48000"
    )


def test_r7_second_pass_report_option_goes_inside_loudnorm() -> None:
    measure = LoudnormMeasure(-30.12, -18.3, 2.4, -40.71, 0.55)
    plain = second_pass_filter(measure)
    reported = second_pass_filter(measure, report=True)
    assert reported == plain.replace(":linear=true", ":linear=true:print_format=json")


def test_r7_parse_report_reads_output_loudness_and_mode() -> None:
    report = parse_report(_JSON.replace('"-14.50"', '"-13.80"'))
    assert report == PlateReport(output_i=-13.80, normalization_type="dynamic")
    with pytest.raises(MediaError):
        parse_report("aucun bloc")


def test_clean_and_render_share_the_silent_track() -> None:
    from rushes.clean import clean

    assert clean.SILENT_TRACK == verify.SILENT_TRACK == "anullsrc=r=48000:cl=stereo"


def test_r7_delivery_constants_have_one_source() -> None:
    assert (CANVAS_W, CANVAS_H) == (verify.WIDTH, verify.HEIGHT)
    args = encode_args(RenderSettings())
    assert args[args.index("-r") + 1] == str(verify.FRAME_RATE)
    assert args[args.index("-ar") + 1] == str(verify.SAMPLE_RATE)
    assert f"aresample={verify.SAMPLE_RATE}" in second_pass_filter(
        LoudnormMeasure(-30, -18, 2, -40, 0)
    )


def test_r7_silent_measure_is_flagged_and_never_reaches_a_filter() -> None:
    silent = parse_measure(_JSON.replace("-30.12", "-inf").replace("-40.71", "-inf"))
    assert silent.silent and math.isinf(silent.input_i)
    with pytest.raises(ValueError):
        second_pass_filter(silent)
    assert LoudnormMeasure(-70.0, -60, 0, -70, 0).silent  # under the loudnorm gate: no gain
    assert not LoudnormMeasure(-30, -18, 2, -40, 0).silent
    with pytest.raises(ValueError):
        second_pass_filter(LoudnormMeasure(-30, float("nan"), 2, -40, 0))


# --- encoding -----------------------------------------------------------------------------------


def test_r7_encode_args_match_the_delivery_spec() -> None:
    args = encode_args(RenderSettings(preset="slow", crf=20))
    assert args == [
        *("-c:v", "libx264", "-preset", "slow", "-crf", "20", "-profile:v", "high"),
        *("-x264-params", "8x8dct=1"),
        *("-pix_fmt", "yuv420p", "-r", "30", "-fps_mode", "cfr"),
        *("-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"),
        *("-movflags", "+faststart", "-threads", "4"),
    ]
