"""Cover (task 8a): three candidates by sharpness and motion, text inside the Instagram crops."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from helpers import synthetic_phash
from rushes.config import Layout
from rushes.context import Context
from rushes.meta.cover import (
    COVER_H,
    COVER_W,
    cover_candidates,
    cover_words,
    render_cover,
    safe_zone,
    segment_keyframes,
)
from rushes.schemas import ScenesFile, Shot

LAYOUTS: list[Layout] = ["square", "portrait", "full", "vertical"]


def noisy(path: Path, seed: int, *, blur: bool = False, size: tuple[int, int] = (320, 180)) -> Path:
    """A frame full of detail (sharp), or the same one blurred away."""
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    image = Image.fromarray(data)
    if blur:
        image = image.resize((8, 4)).resize(size)
    image.save(path)
    return path


def grainy(path: Path, seed: int, amplitude: float) -> Path:
    rng = np.random.default_rng(seed)
    data = 128 + (rng.integers(-128, 128, (180, 320, 3)) * amplitude).astype(int)
    Image.fromarray(data.astype(np.uint8)).save(path)
    return path


def flat(path: Path, size: tuple[int, int] = (320, 180)) -> Path:
    Image.new("RGB", size, (128, 128, 128)).save(path)
    return path


def test_cover_three_candidates_best_chosen(tmp_path: Path) -> None:
    keyframes = [
        (1000, noisy(tmp_path / "a.png", 1, blur=True)),
        (2000, noisy(tmp_path / "b.png", 2)),
        (3000, flat(tmp_path / "c.png")),
        (4000, noisy(tmp_path / "d.png", 3)),
        (5000, noisy(tmp_path / "e.png", 4, blur=True)),
    ]
    found = cover_candidates(keyframes)
    assert len(found) == 3
    times = [t for t, _ in found]
    assert set(times[:2]) == {2000, 4000}  # the two sharp frames lead
    scores = [s for _, s in found]
    assert scores == sorted(scores, reverse=True)
    assert max(scores) == pytest.approx(0.5)  # the best frame is 1.0 sharp x (0.5 + 0 motion)


def test_cover_motion_weighs_in_the_score(tmp_path: Path) -> None:
    sharp = noisy(tmp_path / "s.png", 1)
    mild = grainy(tmp_path / "m.png", 2, 0.7)  # variance about half of the sharp one
    frames = [(1000, sharp), (2000, mild)]
    still = cover_candidates(frames)
    assert [t for t, _ in still] == [1000, 2000]
    moving = cover_candidates(frames, motion={2000: 1.0})
    assert [t for t, _ in moving] == [2000, 1000]  # motion lifts the duller frame above
    assert moving[1][1] == pytest.approx(0.5)  # sharpness x (0.5 + 0)
    assert 0.5 < moving[0][1] < 1.5
    only = cover_candidates([(1000, sharp)], motion={1000: 1.0})
    assert only[0][1] == pytest.approx(1.5)


def test_cover_motion_outside_0_1_is_clamped(tmp_path: Path) -> None:
    sharp = noisy(tmp_path / "s.png", 1)
    assert cover_candidates([(0, sharp)], motion={0: 7.0})[0][1] == pytest.approx(1.5)
    assert cover_candidates([(0, sharp)], motion={0: -3.0})[0][1] == pytest.approx(0.5)


def test_cover_candidates_ties_keep_the_earliest_and_n_is_honoured(tmp_path: Path) -> None:
    same = flat(tmp_path / "f.png")
    found = cover_candidates([(3000, same), (1000, same), (2000, same)], n=2)
    assert [t for t, _ in found] == [1000, 2000]


def test_cover_fewer_keyframes_than_candidates(tmp_path: Path) -> None:
    assert len(cover_candidates([(10, noisy(tmp_path / "a.png", 1))])) == 1
    assert cover_candidates([]) == []


def test_cover_unreadable_frames_are_skipped(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    found = cover_candidates(
        [(1, broken), (2, tmp_path / "missing.png"), (3, flat(tmp_path / "f.png"))]
    )
    assert [t for t, _ in found] == [3]


def test_cover_candidates_are_reproducible(tmp_path: Path) -> None:
    keyframes = [(i * 1000, noisy(tmp_path / f"{i}.png", i)) for i in range(5)]
    assert cover_candidates(keyframes) == cover_candidates(keyframes)


def _shot(i: int, a: int, b: int, key: int, motion: float) -> Shot:
    return Shot(id=i, start_ms=a, end_ms=b, keyframe_ms=key, motion=motion, loudness_lufs=None,
                speech_ratio=0.0, phash=synthetic_phash(i))  # fmt: skip


def _scenes(*shots: Shot) -> ScenesFile:
    return ScenesFile(version=1, episode_id=1, duration_ms=60_000, detector={}, shots=list(shots))


def test_segment_keyframes_keep_the_shots_of_the_span() -> None:
    scenes = _scenes(
        _shot(0, 0, 4000, 2000, 0.1),
        _shot(1, 4000, 8000, 6000, 0.9),
        _shot(2, 8000, 12000, 10000, 0.5),
        _shot(3, 12000, 20000, 16000, 0.2),
    )
    assert segment_keyframes(scenes, (5000, 13000)) == [(6000, 0.9), (10000, 0.5), (12999, 0.2)]


def test_segment_keyframes_clamp_a_keyframe_outside_the_span() -> None:
    scenes = _scenes(_shot(0, 0, 10_000, 5000, 0.4))
    assert segment_keyframes(scenes, (7000, 9000)) == [(7000, 0.4)]
    assert segment_keyframes(scenes, (0, 3000)) == [(2999, 0.4)]


def test_segment_keyframes_without_scenes_is_empty() -> None:
    assert segment_keyframes(None, (0, 1000)) == []
    assert segment_keyframes(_scenes(), (0, 1000)) == []


def test_cover_words_first_four_words_of_the_hook_else_the_name() -> None:
    assert cover_words("Wait for the ending 😭 please", "Tom and Jerry") == "Wait for the ending"
    assert cover_words(None, "Tom and Jerry") == "Tom and Jerry"
    assert cover_words("Wow!", "Tom and Jerry") == "Tom and Jerry"  # fewer than 2 words
    assert cover_words("  ", "Squeezie") == "Squeezie"
    assert cover_words(None, "A B C D E F") == "A B C D"


def _bbox(path: Path, background: int = 128) -> tuple[int, int, int, int]:
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mask = np.abs(pixels - background).max(axis=2) > 60
    assert mask.any(), "no text drawn"
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


@pytest.mark.parametrize("layout", LAYOUTS)
def test_cover_is_1080x1920_with_text_in_the_instagram_safe_zone(
    ctx: Context, tmp_path: Path, layout: Layout
) -> None:
    out = tmp_path / "cover.jpg"
    charter = ctx.config.charters["saturday_toons"]
    render_cover(flat(tmp_path / "f.png", (640, 360)), layout=layout, words="WAIT FOR THE ENDING",
                 part_number=2, charter=charter, language="en", out=out)  # fmt: skip
    with Image.open(out) as image:
        assert image.size == (COVER_W, COVER_H) == (1080, 1920)
        assert image.format == "JPEG"
    zone = safe_zone(charter)
    left, top, right, bottom = _bbox(out)
    assert zone.x - 2 <= left and right <= zone.x + zone.w + 2
    assert zone.y - 2 <= top and bottom <= zone.y + zone.h + 2
    # inside both Instagram crops of a 9:16 cover: 4:5 (1350 px tall) and 3:4 (1440 px tall)
    assert top >= 285 and bottom <= 1920 - 285
    assert top >= 240 and bottom <= 1920 - 240
    assert (bottom - top) > 150  # a big word block, not a speck


def test_safe_zone_respects_the_charter_margins(ctx: Context) -> None:
    charter = ctx.config.charters["saturday_toons"]
    zone = safe_zone(charter)
    assert zone.y >= 285 and zone.y + zone.h <= 1920 - charter.safe.bottom
    assert zone.x >= max(charter.safe.left, charter.safe.right)
    assert zone.x + zone.w <= 1080 - max(charter.safe.left, charter.safe.right)


def test_cover_part_label_follows_the_language(ctx: Context, tmp_path: Path) -> None:
    charter = ctx.config.charters["instant_culte"]
    frame = flat(tmp_path / "f.png")
    with_part = tmp_path / "a.jpg"
    without = tmp_path / "b.jpg"
    render_cover(frame, layout="square", words="Qui gagne", part_number=3, charter=charter,
                 language="fr", out=with_part)  # fmt: skip
    render_cover(frame, layout="square", words="Qui gagne", part_number=None, charter=charter,
                 language="fr", out=without)  # fmt: skip
    assert _bbox(with_part)[3] > _bbox(without)[3]  # the label sits under the words


def test_cover_is_deterministic_and_written_atomically(ctx: Context, tmp_path: Path) -> None:
    charter = ctx.config.charters["saturday_toons"]
    frame = noisy(tmp_path / "n.png", 5)
    a, b = tmp_path / "out" / "a.jpg", tmp_path / "out" / "b.jpg"
    for out in (a, b):
        render_cover(frame, layout="portrait", words="Hello there", part_number=1,
                     charter=charter, language="en", out=out)  # fmt: skip
    assert a.read_bytes() == b.read_bytes()
    assert sorted(p.name for p in a.parent.iterdir()) == ["a.jpg", "b.jpg"]  # no temporary left


def test_cover_words_with_emoji_and_accents_render(ctx: Context, tmp_path: Path) -> None:
    charter = ctx.config.charters["instant_culte"]
    out = tmp_path / "c.jpg"
    words = "Éléonore 😭 ça marche"
    render_cover(flat(tmp_path / "f.png"), layout="square", words=words, part_number=None,
                 charter=charter, language="fr", out=out)  # fmt: skip
    assert _bbox(out)[2] > _bbox(out)[0]


def test_cover_very_long_words_still_fit_the_zone(ctx: Context, tmp_path: Path) -> None:
    charter = ctx.config.charters["saturday_toons"]
    out = tmp_path / "c.jpg"
    words = "Supercalifragilisticexpialidocious " * 3
    render_cover(flat(tmp_path / "f.png"), layout="square", words=words, part_number=12,
                 charter=charter, language="en", out=out)  # fmt: skip
    zone = safe_zone(charter)
    left, _, right, bottom = _bbox(out)
    assert zone.x - 2 <= left and right <= zone.x + zone.w + 2 and bottom <= zone.y + zone.h + 2


def test_cover_refuses_blank_words(ctx: Context, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_cover(flat(tmp_path / "f.png"), layout="square", words="  ", part_number=None,
                     charter=ctx.config.charters["saturday_toons"], language="en",
                     out=tmp_path / "x.jpg")  # fmt: skip
    assert not (tmp_path / "x.jpg").exists()


def test_cover_unreadable_frame_is_an_error_and_leaves_no_file(
    ctx: Context, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    with pytest.raises(OSError):
        render_cover(bad, layout="square", words="Hi there", part_number=None,
                     charter=ctx.config.charters["saturday_toons"], language="en",
                     out=tmp_path / "x.jpg")  # fmt: skip
    assert not (tmp_path / "x.jpg").exists()
