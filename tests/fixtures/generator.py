"""Synthetic videos for the media tests (docs/plans/00-master.md section 6).

Only filters that every FFmpeg build has: color, testsrc2, sine, anoisesrc, drawbox, overlay,
volume, amix (no drawtext: C36). Levels, so that the doubles recognize them: background tone
`sine` at 0.1, beeps = 1000 Hz sine at 0.9, speech-like = pink `anoisesrc` at 0.5; each source is
muted while a louder one plays, silence and black are exact (to a millisecond / a frame).
"""

import hashlib
import itertools
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rushes.core.intervals import Span
from rushes.media.ffmpeg import run_ffmpeg

# one background colour per shot, plus seeded boxes of random place and colour: a flat picture has
# unstable picture-hash bits, a textured one (different for each shot) hashes alike for a whole shot
_PALETTE = ("0x2b5d9b", "0x9b2b3c", "0x2b9b4f", "0x9b8a2b", "0x6b2b9b", "0x2b9b94")
_BOXES = 12
_RATE = 48_000


@dataclass(frozen=True)
class SynthSpec:
    duration_ms: int
    size: tuple[int, int] = (1280, 720)
    fps: int = 30
    shots_ms: Sequence[int] = ()  # instants of a shot change (other background colour)
    black_ms: Sequence[Span] = ()
    silence_ms: Sequence[Span] = ()
    tone_hz: int = 440
    beeps_ms: Sequence[Span] = ()  # sound peaks
    speech_like_ms: Sequence[Span] = ()  # modulated noise that the VAD double recognizes
    title_card_ms: Span | None = None  # frozen picture: uniform colour and boxes, no text
    logo_corner: Literal["tl", "tr", "bl", "br"] | None = None
    moving_target: Sequence[tuple[int, int]] = ()  # (t_ms, x_px) of a moving square
    face_image: Path | None = None  # photo for YuNet (slow tests)
    sar: tuple[int, int] = (1, 1)  # pixel aspect ratio of the stream
    vfr: bool = False  # variable frame rate (frames dropped, timestamps kept)
    audio: bool = True


Synth = Callable[[SynthSpec], Path]


def _s(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _window(spans: Sequence[Span]) -> str:
    """Expression of `t` (seconds): 1 inside one of the spans, else 0."""
    terms = "+".join(f"gte(t,{_s(a)})*lt(t,{_s(b)})" for a, b in spans)
    return f"gt({terms},0)"


def _fill(color: str, enable: str, x: str = "0", y: str = "0", w: str = "iw", h: str = "ih") -> str:
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill:enable='{enable}'"


def _path_expr(points: Sequence[tuple[int, int]]) -> str:
    """Piecewise-linear x(t) through (t_ms, x_px) points, flat outside them."""
    expr = str(points[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(itertools.pairwise(points))):
        slope = f"{x0}+({x1}-{x0})*(t-{_s(t0)})/{_s(t1 - t0)}"
        expr = f"if(lt(t,{_s(t1)}),{slope},{expr})"
    return f"if(lt(t,{_s(points[0][0])}),{points[0][1]},{expr})" if len(points) > 1 else expr


def _video_graph(spec: SynthSpec, extra_inputs: dict[str, int]) -> str:
    width, height = spec.size
    end = spec.duration_ms
    bounds = [0, *spec.shots_ms, end]
    steps = ["[0:v]null"]
    for index, (a, b) in enumerate(itertools.pairwise(bounds)):
        color = _PALETTE[index % len(_PALETTE)]
        window = f"gte(t,{_s(a)})*lt(t,{_s(b)})"
        steps.append(_fill(color, window))
        rng = random.Random(1000 + index)
        for _ in range(_BOXES):
            x, y = rng.uniform(0, 0.8), rng.uniform(0, 0.8)
            w, h = rng.uniform(0.1, 0.45), rng.uniform(0.1, 0.45)
            steps.append(
                _fill(
                    f"0x{rng.randrange(1 << 24):06x}",
                    window,
                    f"iw*{x:.3f}",
                    f"ih*{y:.3f}",
                    f"iw*{w:.3f}",
                    f"ih*{h:.3f}",
                )
            )
    graph = [",".join(steps) + "[base]"]
    last = "base"
    # moving test pattern in the lower strip (motion for the shot and freeze detectors)
    graph.append(
        f"[1:v]scale=iw:ih[pattern];[{last}][pattern]"
        f"overlay=x='mod(t*{width // 6},W-w)':y='H-h-{max(height // 40, 1)}'[v1]"
    )
    last = "v1"
    if spec.moving_target:
        graph.append(
            f"[{last}][{extra_inputs['target']}:v]overlay=x='{_path_expr(spec.moving_target)}'"
            f":y='(H-h)/2'[v2]"
        )
        last = "v2"
    if spec.face_image is not None:
        graph.append(f"[{last}][{extra_inputs['face']}:v]overlay=x='(W-w)/2':y='(H-h)/2'[v3]")
        last = "v3"
    tail: list[str] = []
    if spec.logo_corner is not None:
        size = max(min(width, height) // 12, 4)
        x = "0" if spec.logo_corner[1] == "l" else f"iw-{size}"
        y = "0" if spec.logo_corner[0] == "t" else f"ih-{size}"
        tail.append(f"drawbox=x={x}:y={y}:w={size}:h={size}:color=white:t=fill")
    if spec.title_card_ms is not None:
        window = _window([spec.title_card_ms])
        tail.append(_fill("0x20242c", window))
        tail.append(_fill("0xd8c070", window, "iw*0.2", "ih*0.4", "iw*0.6", "ih*0.2"))
        tail.append(_fill("0x20242c", window, "iw*0.3", "ih*0.45", "iw*0.4", "ih*0.1"))
    if spec.black_ms:
        tail.append(_fill("black", _window(spec.black_ms)))
    tail.append(f"setsar={spec.sar[0]}/{spec.sar[1]}")
    tail.append("format=yuv420p")
    if spec.vfr:
        tail.append("select='not(between(mod(n,10),1,3))'")  # 3 frames of 10 dropped
    graph.append(f"[{last}]{','.join(tail)}[vout]")
    return ";".join(graph)


def _audio_graph(spec: SynthSpec, first: int) -> tuple[str, int]:
    """Filter chains for the audio inputs starting at index `first`; returns (graph, next index)."""
    loud = [*spec.beeps_ms, *spec.speech_like_ms, *spec.silence_ms]
    chains: list[str] = []
    labels: list[str] = []
    ms = "asetnsamples=n=48:p=0"  # 1 ms frames: the gain changes are exact to the millisecond
    index = first
    tone = (
        f"[{index}:a]{ms},volume='0.8*(1-{_window(loud)})':eval=frame[tone]"
        if loud
        else (f"[{index}:a]volume=0.8[tone]")
    )
    chains.append(tone)
    labels.append("[tone]")
    index += 1
    if spec.beeps_ms:  # sine is 1/8 of full scale: x7.2 gives 0.9
        gain = f"7.2*{_window(spec.beeps_ms)}"
        chains.append(f"[{index}:a]{ms},volume='{gain}':eval=frame[beep]")
        labels.append("[beep]")
        index += 1
    if spec.speech_like_ms:
        chains.append(f"[{index}:a]{ms},volume='{_window(spec.speech_like_ms)}':eval=frame[speech]")
        labels.append("[speech]")
        index += 1
    mix = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest"
    if spec.silence_ms:
        mix += f",volume='1-{_window(spec.silence_ms)}':eval=frame"
    chains.append(mix + ",aformat=channel_layouts=mono[aout]")
    return ";".join(chains), index


def make_video(spec: SynthSpec, out: Path) -> Path:
    width, height = spec.size
    seconds = _s(spec.duration_ms)
    fps = spec.fps
    pattern = (max(width // 10, 16) // 2 * 2, max(height // 10, 16) // 2 * 2)
    args: list[str | Path] = [
        *("-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={seconds}"),
        *("-f", "lavfi", "-i", f"testsrc2=s={pattern[0]}x{pattern[1]}:r={fps}:d={seconds}"),
    ]
    inputs = 2
    extra: dict[str, int] = {}
    if spec.moving_target:
        side = max(min(width, height) // 8, 8) // 2 * 2
        args += ["-f", "lavfi", "-i", f"color=c=white:s={side}x{side}:r={fps}:d={seconds}"]
        extra["target"] = inputs
        inputs += 1
    if spec.face_image is not None:
        args += ["-loop", "1", "-framerate", str(fps), "-t", seconds, "-i", spec.face_image]
        extra["face"] = inputs
        inputs += 1
    graph = _video_graph(spec, extra)
    maps: list[str | Path] = ["-map", "[vout]"]
    if spec.audio:
        first = inputs
        args += ["-f", "lavfi", "-i", f"sine=f={spec.tone_hz}:r={_RATE}:d={seconds}"]
        if spec.beeps_ms:
            args += ["-f", "lavfi", "-i", f"sine=f=1000:r={_RATE}:d={seconds}"]
        if spec.speech_like_ms:
            args += ["-f", "lavfi", "-i", f"anoisesrc=c=pink:a=0.5:r={_RATE}:d={seconds}"]
        audio_graph, _ = _audio_graph(spec, first)
        graph += ";" + audio_graph
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "96k", "-ar", str(_RATE)]
    args += ["-filter_complex", graph, *maps]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-g", str(fps)]
    args += ["-fps_mode", "vfr" if spec.vfr else "cfr", "-t", seconds]
    args += ["-movflags", "+faststart", "-f", "mp4", out]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    return out


def cache_key(spec: SynthSpec) -> str:
    return hashlib.sha256(repr(spec).encode()).hexdigest()[:16]
