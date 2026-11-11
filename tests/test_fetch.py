"""`models fetch` and `models check` (task 9): sha256-verified, atomic, no network in tests."""

import fcntl
import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from fakes.net import FakeHttp
from rushes.errors import RushesError
from rushes.models.fetch import ModelFile, check, fetch, load_manifest
from rushes.paths import Paths


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def direct(name: str, leaf: str, data: bytes, url: str | None = None) -> ModelFile:
    return ModelFile(
        name=name,
        file=f"{name}/{leaf}",
        url=url or f"https://example.test/{name}/{leaf}",
        sha256=sha(data),
        bytes=len(data),
        license="MIT",
    )


def make_archive(members: dict[str, bytes], extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data in {**members, **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def archived(
    name: str,
    archive: bytes,
    members: dict[str, bytes],
    url: str = "https://example.test/a.tar.bz2",
) -> list[ModelFile]:
    return [
        ModelFile(
            name=name,
            file=f"{name}/{Path(member).name}",
            url=url,
            sha256=sha(data),
            bytes=len(data),
            license="MIT",
            archive_member=member,
            archive_sha256=sha(archive),
            archive_bytes=len(archive),
        )
        for member, data in members.items()
    ]


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data")


def leftovers(paths: Paths) -> list[str]:
    root = paths.cache_models
    return (
        sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and p.name != ".fetch.lock"  # the lock file is not a leftover
        )
        if root.exists()
        else []
    )


def test_fetch_downloads_and_verifies_sha256(paths: Paths) -> None:
    data = b"weights"
    entry = direct("clip", "m.bin", data)
    http = FakeHttp(blobs={entry.url: data})
    assert fetch(http, paths, manifest=[entry]) == ["clip"]
    assert (paths.cache_models / "clip" / "m.bin").read_bytes() == data
    assert leftovers(paths) == ["clip/m.bin"]


def test_fetch_verifies_sha256(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"expected")
    http = FakeHttp(blobs={entry.url: b"tampered"})
    with pytest.raises(RushesError, match="empreinte incorrecte pour clip"):
        fetch(http, paths, manifest=[entry])
    assert leftovers(paths) == []  # nothing kept, not even a temporary file


def test_fetch_skips_present_valid_file(paths: Paths) -> None:
    data = b"weights"
    entry = direct("clip", "m.bin", data)
    target = paths.cache_models / "clip" / "m.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    http = FakeHttp()
    assert fetch(http, paths, manifest=[entry]) == []
    assert http.downloads == []


def test_fetch_replaces_a_file_with_a_wrong_sha256(paths: Paths) -> None:
    data = b"weights"
    entry = direct("clip", "m.bin", data)
    target = paths.cache_models / "clip" / "m.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")
    assert fetch(FakeHttp(blobs={entry.url: data}), paths, manifest=[entry]) == ["clip"]
    assert target.read_bytes() == data


def test_fetch_failed_replacement_leaves_no_wrong_file(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"expected")
    target = paths.cache_models / "clip" / "m.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")
    with pytest.raises(RushesError, match="empreinte incorrecte"):
        fetch(FakeHttp(blobs={entry.url: b"tampered"}), paths, manifest=[entry])
    assert not target.exists()


def test_fetch_only_selects_models_and_rejects_unknown_names(paths: Paths) -> None:
    one, two = direct("one", "a.bin", b"1"), direct("two", "b.bin", b"2")
    http = FakeHttp(blobs={one.url: b"1", two.url: b"2"})
    assert fetch(http, paths, only=["two"], manifest=[one, two]) == ["two"]
    assert [url for url, _ in http.downloads] == [two.url]
    with pytest.raises(RushesError, match="inconnu") as excinfo:
        fetch(http, paths, only=["nope"], manifest=[one, two])
    assert "one" in (excinfo.value.hint or "") and "two" in (excinfo.value.hint or "")


def test_fetch_reports_each_model_once_in_manifest_order(paths: Paths) -> None:
    files = [direct("one", "a.bin", b"1"), direct("one", "b.bin", b"11"), direct("two", "c", b"2")]
    http = FakeHttp(blobs={e.url: b"1" * e.bytes if e.name == "one" else b"2" for e in files})
    assert fetch(http, paths, manifest=files) == ["one", "two"]


def test_fetch_downloads_a_shared_archive_once_and_extracts_only_listed_members(
    paths: Paths,
) -> None:
    members = {"root/model.onnx": b"onnx", "root/labels.csv": b"a,b"}
    archive = make_archive(members, extra={"root/README.md": b"not listed"})
    entries = archived("ced", archive, members)
    http = FakeHttp(blobs={entries[0].url: archive})
    assert fetch(http, paths, manifest=entries) == ["ced"]
    assert len(http.downloads) == 1
    assert (paths.cache_models / "ced" / "model.onnx").read_bytes() == b"onnx"
    assert (paths.cache_models / "ced" / "labels.csv").read_bytes() == b"a,b"
    assert leftovers(paths) == ["ced/labels.csv", "ced/model.onnx"]  # archive and extras are gone


def test_fetch_archive_only_extracts_what_is_missing(paths: Paths) -> None:
    members = {"root/model.onnx": b"onnx", "root/labels.csv": b"a,b"}
    archive = make_archive(members)
    entries = archived("ced", archive, members)
    present = paths.cache_models / "ced" / "model.onnx"
    present.parent.mkdir(parents=True)
    present.write_bytes(b"onnx")
    before = present.stat().st_mtime_ns
    assert fetch(FakeHttp(blobs={entries[0].url: archive}), paths, manifest=entries) == ["ced"]
    assert present.stat().st_mtime_ns == before


def test_fetch_archive_skipped_when_every_member_is_present(paths: Paths) -> None:
    members = {"root/model.onnx": b"onnx"}
    archive = make_archive(members)
    entries = archived("ced", archive, members)
    target = paths.cache_models / "ced" / "model.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"onnx")
    http = FakeHttp()
    assert fetch(http, paths, manifest=entries) == []
    assert http.downloads == []


def test_fetch_archive_hash_is_checked_before_extracting(paths: Paths) -> None:
    members = {"root/model.onnx": b"onnx"}
    archive = make_archive(members)
    entries = archived("ced", archive, members)
    tampered = archive[:-1] + bytes([archive[-1] ^ 1])  # same size, other content
    http = FakeHttp(blobs={entries[0].url: tampered})
    with pytest.raises(RushesError, match="empreinte incorrecte pour ced"):
        fetch(http, paths, manifest=entries)
    assert leftovers(paths) == []
    assert not (paths.cache_models / "ced").exists()


def test_fetch_archive_member_with_wrong_sha256_is_refused(paths: Paths) -> None:
    members = {"root/model.onnx": b"onnx"}
    archive = make_archive(members)
    (entry,) = archived("ced", archive, members)
    wrong = ModelFile(**{**entry.__dict__, "sha256": sha(b"other")})
    with pytest.raises(RushesError, match="empreinte incorrecte pour ced"):
        fetch(FakeHttp(blobs={wrong.url: archive}), paths, manifest=[wrong])
    assert leftovers(paths) == []


def test_fetch_archive_member_missing_from_the_archive(paths: Paths) -> None:
    archive = make_archive({"root/other": b"x"})
    (entry,) = archived("ced", archive, {"root/model.onnx": b"onnx"})
    entry = ModelFile(
        **{**entry.__dict__, "archive_sha256": sha(archive), "archive_bytes": len(archive)}
    )
    with pytest.raises(RushesError, match="ced"):
        fetch(FakeHttp(blobs={entry.url: archive}), paths, manifest=[entry])
    assert leftovers(paths) == []


@pytest.mark.parametrize("member", ["/etc/passwd", "../escape", "root/../../escape"])
def test_fetch_archive_refuses_unsafe_member_names(paths: Paths, member: str) -> None:
    archive = make_archive({member: b"x"})
    entries = archived("ced", archive, {member: b"x"})
    with pytest.raises(RushesError, match="membre d'archive invalide"):
        fetch(FakeHttp(blobs={entries[0].url: archive}), paths, manifest=entries)
    assert leftovers(paths) == []
    assert not (paths.data_dir.parent / "escape").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_fetch_archive_refuses_links(paths: Paths, kind: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        info = tarfile.TarInfo("root/model.onnx")
        info.type = kind
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    archive = buffer.getvalue()
    entries = archived("ced", archive, {"root/model.onnx": b""})
    with pytest.raises(RushesError, match="ced"):
        fetch(FakeHttp(blobs={entries[0].url: archive}), paths, manifest=entries)
    assert leftovers(paths) == []


def test_fetch_refuses_a_manifest_file_outside_the_cache(paths: Paths) -> None:
    entry = ModelFile(**{**direct("clip", "m.bin", b"x").__dict__, "file": "../outside.bin"})
    with pytest.raises(RushesError, match="clip"):
        fetch(FakeHttp(blobs={entry.url: b"x"}), paths, manifest=[entry])


def test_models_check_reports_missing_without_network(paths: Paths) -> None:
    result = check(paths)  # the real manifest, an empty cache; the autouse fixture blocks sockets
    assert {entry.file for entry in load_manifest()} == set(result)
    assert set(result.values()) == {"missing"}


def test_check_reports_ok_missing_and_bad_sha256(paths: Paths) -> None:
    good, bad, absent = (direct("m", leaf, leaf.encode()) for leaf in ("good", "bad", "absent"))
    folder = paths.cache_models / "m"
    folder.mkdir(parents=True)
    (folder / "good").write_bytes(b"good")
    (folder / "bad").write_bytes(b"BAD!")
    assert check(paths, manifest=[good, bad, absent]) == {
        "m/good": "ok",
        "m/bad": "bad_sha256",
        "m/absent": "missing",
    }


def test_fetch_archive_shared_by_two_models_is_downloaded_once(paths: Paths) -> None:
    members = {"root/a.onnx": b"a", "root/b.onnx": b"b"}
    archive = make_archive(members)
    first, second = archived("one", archive, members)
    second = ModelFile(**{**second.__dict__, "name": "two", "file": "two/b.onnx"})
    http = FakeHttp(blobs={first.url: archive})
    assert fetch(http, paths, manifest=[first, second]) == ["one", "two"]
    assert len(http.downloads) == 1
    assert (paths.cache_models / "one" / "a.onnx").read_bytes() == b"a"
    assert (paths.cache_models / "two" / "b.onnx").read_bytes() == b"b"


def test_fetch_calls_progress_before_each_download(paths: Paths) -> None:
    one, two = direct("one", "a", b"1"), direct("two", "b", b"2")
    seen: list[str] = []
    http = FakeHttp(blobs={one.url: b"1", two.url: b"2"})
    fetch(http, paths, manifest=[one, two], progress=seen.append)
    assert seen == ["one", "two"]


def test_fetch_asks_the_download_to_stop_at_the_expected_size(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"weights")
    members = {"root/model.onnx": b"onnx"}
    archive = make_archive(members)
    (member,) = archived("ced", archive, members)
    http = FakeHttp(blobs={entry.url: b"weights", member.url: archive})
    fetch(http, paths, manifest=[entry, member])
    assert http.max_bytes == [len(b"weights"), len(archive)]  # exactly: one byte more is an error


@pytest.mark.parametrize("blob", [b"short", b"far too long a file"], ids=["short", "long"])
def test_fetch_refuses_a_download_of_the_wrong_size_before_the_hash(
    paths: Paths, blob: bytes
) -> None:
    entry = direct("clip", "m.bin", b"expected")
    with pytest.raises(RushesError, match="téléchargement incomplet ou trop long pour clip") as e:
        fetch(FakeHttp(blobs={entry.url: blob}), paths, manifest=[entry])
    assert "empreinte" not in str(e.value)  # that message is for a right size and a wrong hash
    assert leftovers(paths) == []


@pytest.mark.parametrize("delta", [-1, 1], ids=["short", "long"])
def test_fetch_refuses_an_archive_of_the_wrong_size_before_the_hash(
    paths: Paths, delta: int
) -> None:
    members = {"root/model.onnx": b"onnx"}
    archive = make_archive(members)
    (entry,) = archived("ced", archive, members)
    blob = archive[:-1] if delta < 0 else archive + b"\0"
    with pytest.raises(RushesError, match="téléchargement incomplet ou trop long pour ced") as e:
        fetch(FakeHttp(blobs={entry.url: blob}), paths, manifest=[entry])
    assert "empreinte" not in str(e.value)
    assert leftovers(paths) == []


def test_fetch_removes_orphan_temporaries_left_by_a_killed_run(paths: Paths) -> None:
    root = paths.cache_models
    orphans = [
        root / ".fetch-abc123" / "archive.part",
        root / "qwen4b" / ".model.gguf.tmp-4242",
        root / "qwen4b" / ".model.gguf.tmp-4242.part",
        root / "ced" / "stray.part",
    ]
    keep = [root / "clip" / "m.bin", root / "clip" / ".keep", root / ".gitkeep"]
    for path in [*orphans, *keep]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    entry = direct("clip", "m.bin", b"x")
    assert fetch(FakeHttp(), paths, manifest=[entry]) == []
    assert not any(path.exists() for path in orphans)
    assert not (root / ".fetch-abc123").exists()
    assert all(path.exists() for path in keep)


def hold_lock(paths: Paths) -> Any:
    """Another `models fetch`: the lock taken through its own file descriptor."""
    paths.cache_models.mkdir(parents=True, exist_ok=True)
    handle = (paths.cache_models / ".fetch.lock").open("a")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def test_fetch_refuses_to_run_while_another_fetch_holds_the_lock(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"x")
    orphan = paths.cache_models / "ced" / "stray.part"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"x")
    http = FakeHttp(blobs={entry.url: b"x"})
    handle = hold_lock(paths)
    try:
        with pytest.raises(RushesError, match="un autre `models fetch` est en cours"):
            fetch(http, paths, manifest=[entry])
    finally:
        handle.close()
    assert http.downloads == []
    assert orphan.exists()  # the other fetch may be writing it: cleanup only under the lock


def test_fetch_releases_the_lock_afterwards_even_on_failure(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"expected")
    with pytest.raises(RushesError, match="empreinte incorrecte"):
        fetch(FakeHttp(blobs={entry.url: b"tampered"}), paths, manifest=[entry])
    hold_lock(paths).close()  # would raise BlockingIOError if the lock were still held
    assert fetch(FakeHttp(blobs={entry.url: b"expected"}), paths, manifest=[entry]) == ["clip"]
    hold_lock(paths).close()


def test_the_lock_file_is_neither_an_orphan_nor_a_model(paths: Paths) -> None:
    entry = direct("clip", "m.bin", b"x")
    fetch(FakeHttp(blobs={entry.url: b"x"}), paths, manifest=[entry])
    assert (paths.cache_models / ".fetch.lock").exists()
    fetch(FakeHttp(), paths, manifest=[entry])  # a second run (cleanup included) keeps it
    assert (paths.cache_models / ".fetch.lock").exists()
    assert set(check(paths, manifest=[entry])) == {"clip/m.bin"}


def test_check_does_not_take_the_lock(paths: Paths) -> None:
    handle = hold_lock(paths)
    try:
        assert check(paths, manifest=[direct("clip", "m.bin", b"x")]) == {"clip/m.bin": "missing"}
    finally:
        handle.close()


def test_fetch_with_an_unknown_size_asks_for_no_bound(paths: Paths) -> None:
    entry = ModelFile(**{**direct("clip", "m.bin", b"x").__dict__, "bytes": 0})
    http = FakeHttp(blobs={entry.url: b"x"})
    assert fetch(http, paths, manifest=[entry]) == ["clip"]
    assert http.max_bytes == [None]  # same rule as the archives: 0 means unknown
