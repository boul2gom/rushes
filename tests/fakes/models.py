"""Model doubles: deterministic stand-ins for the local models (docs/plans/00-master.md 5.5)."""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from fixtures.generator import LABEL_CELLS, LABEL_GRID, label_bits, label_code, label_square
from rushes.core.intervals import Span
from rushes.models.clip import MODEL_ID

SAMPLE_RATE = 16_000
WINDOW_MS = 20
MIN_SPEECH_MS = 250
MIN_RMS = 0.1  # brief: 0.25, but white noise at 0.5 has RMS 0.155 once resampled to 16 kHz
MIN_ZERO_CROSSINGS = 0.2


class FakeVad:
    """Speech = 20 ms windows with RMS > 0.1 and zero-crossing rate > 0.2, runs of >= 250 ms.

    Recognizes the generator's `speech_like_ms` (white noise at 0.5) and ignores its 1000 Hz beeps
    (loud but a low crossing rate) and the background tone (RMS 0.07).
    """

    def speech(self, pcm16k: NDArray[np.float32]) -> list[Span]:
        size = SAMPLE_RATE * WINDOW_MS // 1000
        count = len(pcm16k) // size
        spans: list[Span] = []
        run_start: int | None = None
        for index in range(count + 1):
            voiced = False
            if index < count:
                window = pcm16k[index * size : (index + 1) * size].astype(np.float64)
                rms = float(np.sqrt(np.mean(window**2)))
                crossings = float(np.mean(np.signbit(window[1:]) != np.signbit(window[:-1])))
                voiced = rms > MIN_RMS and crossings > MIN_ZERO_CROSSINGS
            if voiced and run_start is None:
                run_start = index
            elif not voiced and run_start is not None:
                start, end = run_start * WINDOW_MS, index * WINDOW_MS
                if end - start >= MIN_SPEECH_MS:
                    spans.append((start, end))
                run_start = None
        return spans


class FakeModelHub:
    """Records releases, loads nothing; `vad()` is the deterministic double."""

    def __init__(self, loaded: Mapping[str, int] | None = None) -> None:
        self._loads = dict(loaded or {})
        self.release_calls = 0
        self._vad: FakeVad | None = None

    def vad(self) -> FakeVad:
        """Loaded once until `release`, like the real hub (R19)."""
        if self._vad is None:
            self._vad = FakeVad()
            self._loads["vad"] = self._loads.get("vad", 0) + 1
        return self._vad

    def loads(self) -> Mapping[str, int]:
        return dict(self._loads)

    def release(self) -> None:
        self.release_calls += 1
        self._vad = None


# --- image encoder double (task 12a) -----------------------------------------------------------

LABEL_AXES = 4096  # a label code is hashed onto one of these axes (tests check no collision)
LABEL_DIM = LABEL_AXES + 1  # plus the axis of "no label"
NO_LABEL = LABEL_DIM - 1
STOP_WORDS = frozenset({"a", "an", "the", "another", "one", "in", "on"})


def label_axis(word: str) -> int:
    """The axis of a word: its 16-bit label code folded onto `LABEL_AXES`."""
    return label_code(word) % LABEL_AXES


def _unit(index: int) -> NDArray[np.float32]:
    vector = np.zeros(LABEL_DIM, dtype=np.float32)
    vector[index] = 1.0
    return vector


def decode_label(path: Path) -> int | None:
    """The 16-bit code of the generator's `shot_labels` pattern in an image, None if there is none.

    The square is at `label_square` (centred); its marker ring must be magenta at the middle of
    each side, then each of the 4x4 code cells is read at its centre (mean of a small patch:
    white = 1, black = 0). Robust to JPEG and to scaling (a cell is read at its centre).
    """
    with Image.open(path) as picture:
        pixels = np.asarray(picture.convert("RGB"), dtype=np.float64)
    height, width = pixels.shape[:2]
    x0, y0, cell = label_square(width, height)
    margin = max(cell // 4, 0)

    def patch(col: int, row: int) -> NDArray[np.float64]:
        left, top = x0 + col * cell + margin, y0 + row * cell + margin
        block = pixels[top : y0 + (row + 1) * cell - margin, left : x0 + (col + 1) * cell - margin]
        return block.reshape(-1, 3).mean(axis=0) if block.size else pixels[top, left]

    middle = LABEL_CELLS // 2
    for col, row in (
        (0, middle),
        (LABEL_CELLS - 1, middle),
        (middle, 0),
        (middle, LABEL_CELLS - 1),
    ):
        red, green, blue = patch(col, row)
        if not (red > 170 and blue > 170 and green < 110):
            return None
    code = 0
    for i in range(LABEL_GRID**2):
        row, col = divmod(i, LABEL_GRID)
        code = code << 1 | int(float(patch(col + 1, row + 1).mean()) > 128)
    return code


def write_label_image(word: str, path: Path, size: tuple[int, int] = (320, 180)) -> Path:
    """An image carrying `word`'s pattern (what a shot labelled `word` looks like): a reference."""
    width, height = size
    x0, y0, cell = label_square(width, height)
    picture = Image.new("RGB", size, (60, 90, 140))
    draw = ImageDraw.Draw(picture)
    side = cell * LABEL_CELLS
    draw.rectangle((x0, y0, x0 + side - 1, y0 + side - 1), fill=(255, 0, 255))
    inner = cell * LABEL_GRID
    draw.rectangle((x0 + cell, y0 + cell, x0 + cell + inner - 1, y0 + cell + inner - 1), fill=0)
    for i, on in enumerate(label_bits(label_code(word))):
        if on:
            row, col = divmod(i, LABEL_GRID)
            left, top = x0 + cell * (col + 1), y0 + cell * (row + 1)
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    picture.save(path)
    return path


class FakeImageEncoder:
    """CLIP double: one axis per word, so a cosine is exactly 0 or a simple fraction.

    An image is the unit vector of the axis of its `shot_labels` pattern (`decode_label`, folded
    onto `LABEL_AXES` axes), or of the "no label" axis when it has none. A text is the normalised
    sum of the unit vectors of its words (upper case, without the stop words), through the same
    `label_axis`: `"a banana"` is the vector of a shot labelled BANANA (cosine 1), `"a cartoon
    character chasing another one"` has 3 words, so a shot labelled "chasing" scores 1/sqrt(3) on
    it and 0 on the other prompts. Two words on one axis would spoil that: test_vision checks that
    the vocabulary of the tests has no collision.
    `image_calls` and `text_calls` record what was encoded (one entry per call).
    """

    dim = LABEL_DIM
    model_name = MODEL_ID

    def __init__(self) -> None:
        self.image_calls: list[list[Path]] = []
        self.text_calls: list[list[str]] = []

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
        self.image_calls.append(list(images))
        rows = []
        for image in images:
            code = decode_label(image)
            rows.append(_unit(NO_LABEL if code is None else code % LABEL_AXES))
        return np.stack(rows) if rows else np.zeros((0, LABEL_DIM), dtype=np.float32)

    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        self.text_calls.append(list(texts))
        rows = []
        for text in texts:
            words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS]
            total = np.zeros(LABEL_DIM, dtype=np.float32)
            for word in words:
                total += _unit(label_axis(word))
            norm = float(np.linalg.norm(total))
            rows.append(total / norm if norm else _unit(NO_LABEL))
        return np.stack(rows)
