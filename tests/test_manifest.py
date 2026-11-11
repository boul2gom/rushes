"""The pinned model manifest (task 9): complete, pinned and safe to extract."""

import re
from pathlib import PurePosixPath
from urllib.parse import urlparse

from rushes.models.fetch import load_manifest

EXPECTED = {
    "whisper_small",
    "whisper_base",
    "clip",
    "ced",
    "yunet",
    "qwen4b",
    "qwen1_7b",
    "supertonic",
    "kokoro",
}
HEX64 = re.compile(r"[0-9a-f]{64}")
COMMIT40 = re.compile(r"[0-9a-f]{40}")


def test_manifest_entries_complete() -> None:
    entries = load_manifest()
    assert {entry.name for entry in entries} == EXPECTED
    assert len({entry.file for entry in entries}) == len(entries)  # one table per file
    for entry in entries:
        assert urlparse(entry.url).scheme == "https", entry
        assert HEX64.fullmatch(entry.sha256), entry
        assert entry.bytes > 0, entry
        assert entry.license.strip(), entry


def test_manifest_files_are_relative_and_in_their_model_folder() -> None:
    for entry in load_manifest():
        path = PurePosixPath(entry.file)
        assert not path.is_absolute() and ".." not in path.parts, entry
        assert path.parts[0] == entry.name, entry  # one sub-folder per model


def test_manifest_urls_are_pinned() -> None:
    for entry in load_manifest():
        url = entry.url
        assert "latest" not in url and "/main/" not in url and "/master/" not in url, entry
        if urlparse(url).netloc == "huggingface.co":
            assert COMMIT40.search(url.split("/resolve/")[1].split("/")[0]), entry  # full commit
        if "github" in urlparse(url).netloc and not entry.archive_member:
            assert COMMIT40.search(url), entry


def test_manifest_whisper_models_carry_all_their_files() -> None:
    files = {entry.file for entry in load_manifest()}
    for name in ("whisper_small", "whisper_base"):
        for leaf in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            assert f"{name}/{leaf}" in files


def test_manifest_archive_members_come_with_the_archive_hash() -> None:
    entries = load_manifest()
    archived = {entry.name for entry in entries if entry.archive_member}
    assert archived == {"ced", "supertonic", "kokoro"}
    for entry in entries:
        assert bool(entry.archive_member) == bool(entry.archive_sha256), entry
        if entry.archive_member:
            assert HEX64.fullmatch(entry.archive_sha256), entry
            assert entry.archive_bytes > 0, entry  # bounds the archive download
            member = PurePosixPath(entry.archive_member)
            assert not member.is_absolute() and ".." not in member.parts, entry
    for name in archived:  # every member of an archive shares its url and archive hash
        group = [entry for entry in entries if entry.name == name]
        assert len({(entry.url, entry.archive_sha256) for entry in group}) == 1


def test_manifest_kokoro_has_the_files_the_loader_opens() -> None:
    files = {entry.file for entry in load_manifest() if entry.name == "kokoro"}
    for leaf in ("model.int8.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt"):
        assert f"kokoro/{leaf}" in files
    assert any(file.startswith("kokoro/espeak-ng-data/") for file in files)
