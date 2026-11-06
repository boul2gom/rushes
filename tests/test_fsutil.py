from pathlib import Path

import pytest

from rushes.fsutil import atomic_path


def test_atomic_path_renames_at_the_end(tmp_path: Path) -> None:
    final = tmp_path / "sub" / "clean.mp4"
    with atomic_path(final) as tmp:
        assert tmp.parent == final.parent and tmp.name.startswith(".clean.mp4.tmp-")
        tmp.write_bytes(b"x")
        assert not final.exists()
    assert final.read_bytes() == b"x"
    assert [p.name for p in final.parent.iterdir()] == ["clean.mp4"]


def test_atomic_path_removes_the_temporary_file_on_error(tmp_path: Path) -> None:
    final = tmp_path / "out.txt"
    with pytest.raises(RuntimeError), atomic_path(final) as tmp:
        tmp.write_text("partial")
        raise RuntimeError("killed")
    assert list(tmp_path.iterdir()) == []


def test_atomic_path_replaces_an_existing_file_and_handles_directories(tmp_path: Path) -> None:
    final = tmp_path / "frames"
    final.mkdir()
    (final / "old.txt").write_text("old")
    with atomic_path(final) as tmp:
        tmp.mkdir()
        (tmp / "new.txt").write_text("new")
    assert [p.name for p in final.iterdir()] == ["new.txt"]


def test_atomic_path_directory_cleaned_on_error(tmp_path: Path) -> None:
    final = tmp_path / "frames"
    with pytest.raises(RuntimeError), atomic_path(final) as tmp:
        tmp.mkdir()
        (tmp / "a.txt").write_text("a")
        raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []
