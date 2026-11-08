from pathlib import Path

import pytest

from rushes.errors import RushesError
from rushes.fsutil import atomic_path, staged


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


def test_staged_files_replace_the_finals_only_on_commit(tmp_path: Path) -> None:
    a, b = tmp_path / "out" / "a.mp4", tmp_path / "out" / "b.mp4"
    a.parent.mkdir()
    a.write_bytes(b"old a")
    with staged(a, b) as stage:
        stage.path(a).write_bytes(b"new a")
        stage.path(b).write_bytes(b"new b")
        assert stage.path(a).parent == a.parent and stage.path(a).name.startswith(".a.mp4.tmp-")
        assert a.read_bytes() == b"old a" and not b.exists()  # nothing replaced before commit
        stage.commit()
        assert (a.read_bytes(), b.read_bytes()) == (b"new a", b"new b")
    assert sorted(p.name for p in a.parent.iterdir()) == ["a.mp4", "b.mp4"]


def test_staged_files_are_dropped_without_commit_and_on_error(tmp_path: Path) -> None:
    final = tmp_path / "a.mp4"
    final.write_bytes(b"old")
    with staged(final) as stage:
        stage.path(final).write_bytes(b"never kept")
    assert final.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["a.mp4"]
    with pytest.raises(RuntimeError), staged(final) as stage:
        stage.path(final).write_bytes(b"partial")
        raise RuntimeError("killed")
    assert final.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["a.mp4"]


def test_staged_commit_twice_is_harmless_and_a_stale_temporary_is_cleared(tmp_path: Path) -> None:
    final = tmp_path / "a.mp4"
    with staged(final) as probe_stage:
        stale = probe_stage.path(final)
    stale.write_bytes(b"left by a killed run")
    with staged(final) as stage:
        assert not stage.path(final).exists()  # cleared when staging starts
        stage.path(final).write_bytes(b"x")
        stage.commit()
        stage.commit()
    assert final.read_bytes() == b"x"


def test_staged_commit_with_a_missing_temporary_raises_before_any_rename(tmp_path: Path) -> None:
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"old a")
    with pytest.raises(RushesError, match="temporaire absent"), staged(a, b) as stage:
        stage.path(a).write_bytes(b"new a")  # b is never written
        stage.commit()
    assert a.read_bytes() == b"old a" and not b.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["a.mp4"]
