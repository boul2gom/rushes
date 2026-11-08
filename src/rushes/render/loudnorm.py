"""Two-pass loudnorm towards -14 LUFS (R7): measure the span, then a linear second pass."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rushes.core.intervals import Span
from rushes.errors import MediaError
from rushes.media.ffmpeg import run_ffmpeg
from rushes.media.verify import SAMPLE_RATE

TARGET_LUFS = -14.0  # R7
TOLERANCE_LU = 1.0  # R7: -14 LUFS +- 1 LU
TARGET = f"I={TARGET_LUFS:g}:TP=-1.5:LRA=11"
PLAIN_RESAMPLE = f"aresample={SAMPLE_RATE}"
SILENCE_FLOOR_LUFS = -70.0  # loudnorm's absolute gate: nothing to measure below it

_BLOCK = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class LoudnormMeasure:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

    @property
    def silent(self) -> bool:
        """No measurable sound (digital silence gives -inf): there is no gain to compute."""
        values = (
            self.input_i,
            self.input_tp,
            self.input_lra,
            self.input_thresh,
            self.target_offset,
        )
        return not all(math.isfinite(v) for v in values) or self.input_i <= SILENCE_FLOOR_LUFS


@dataclass(frozen=True)
class PlateReport:
    """What the second pass printed: loudness it reached and the mode it ran in."""

    output_i: float
    normalization_type: str  # "linear" or "dynamic" (loudnorm refused the linear gain)


def _last_block[T](log: str, build: Callable[[dict[str, str]], T], what: str) -> T:
    for block in reversed(_BLOCK.findall(log)):
        try:
            return build(json.loads(block))
        except ValueError, KeyError, TypeError:
            continue
    raise MediaError(f"{what} loudnorm introuvable dans la sortie d'ffmpeg")


def parse_measure(log: str) -> LoudnormMeasure:
    """The measure from the JSON block loudnorm prints at the end of ffmpeg's log."""
    return _last_block(
        log,
        lambda d: LoudnormMeasure(
            input_i=float(d["input_i"]),
            input_tp=float(d["input_tp"]),
            input_lra=float(d["input_lra"]),
            input_thresh=float(d["input_thresh"]),
            target_offset=float(d["target_offset"]),
        ),
        "mesure",
    )


def parse_report(log: str) -> PlateReport:
    """`output_i` and `normalization_type` of the second pass (`print_format=json`)."""
    return _last_block(
        log,
        lambda d: PlateReport(float(d["output_i"]), str(d["normalization_type"])),
        "rapport",
    )


def measure(src: Path, span: Span) -> LoudnormMeasure:
    """First pass on the audio of `span` (ms) only."""
    args: list[str | Path] = [
        "-ss",
        f"{span[0] / 1000:.3f}",
        "-t",
        f"{(span[1] - span[0]) / 1000:.3f}",
    ]
    args += ["-i", src, "-vn", "-map", "0:a:0", "-af", f"loudnorm={TARGET}:print_format=json"]
    return parse_measure(run_ffmpeg([*args, "-f", "null", "-"]))


def second_pass_filter(m: LoudnormMeasure, *, report: bool = False) -> str:
    """Linear second pass with the measured values (fixed decimals, never inf or nan).

    `report`: loudnorm also prints its result as JSON (see `parse_report`)."""
    if m.silent:
        raise ValueError("mesure silencieuse ou non finie : pas de passe loudnorm")
    shown = ":print_format=json" if report else ""
    return (
        f"loudnorm={TARGET}:measured_I={m.input_i:.2f}:measured_TP={m.input_tp:.2f}:"
        f"measured_LRA={m.input_lra:.2f}:measured_thresh={m.input_thresh:.2f}:"
        f"offset={m.target_offset:.2f}:linear=true{shown},{PLAIN_RESAMPLE}"
    )
