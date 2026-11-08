"""Delivery check helpers that need no video: MP4 atoms built byte by byte."""

import struct
from pathlib import Path

import pytest

from rushes.errors import MediaError
from rushes.media.verify import top_level_atoms


def _atom(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


FTYP = _atom(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")


def _write(tmp_path: Path, *chunks: bytes) -> Path:
    path = tmp_path / "x.mp4"
    path.write_bytes(b"".join(chunks))
    return path


def test_top_level_atoms_lists_types_in_order_with_the_ftyp_brand(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        FTYP,
        _atom(b"free", b"\x00" * 5),
        _atom(b"moov", b"m" * 20),
        _atom(b"mdat", b"d" * 40),
    )
    atoms = top_level_atoms(path)
    assert [kind for kind, _ in atoms] == ["ftyp", "free", "moov", "mdat"]
    assert atoms[0][1][:4] == b"isom"


def test_top_level_atoms_reads_a_64_bit_size(tmp_path: Path) -> None:
    big_mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + 30) + b"d" * 30
    path = _write(tmp_path, FTYP, big_mdat, _atom(b"moov", b"m" * 8))
    assert [kind for kind, _ in top_level_atoms(path)] == ["ftyp", "mdat", "moov"]


def test_top_level_atoms_size_zero_runs_to_the_end_of_the_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path, FTYP, _atom(b"moov", b"m" * 8), struct.pack(">I4s", 0, b"mdat") + b"d" * 99
    )
    assert [kind for kind, _ in top_level_atoms(path)] == ["ftyp", "moov", "mdat"]


@pytest.mark.parametrize(
    "tail",
    [
        b"\x00\x00\x00",  # header cut inside the size
        struct.pack(">I", 8) + b"fr",  # header cut inside the type: only 6 bytes
        struct.pack(">I4s", 1, b"free") + b"\x00\x00\x00",  # 64-bit size cut short
        struct.pack(">I4s", 500, b"mdat") + b"d" * 10,  # declares more bytes than the file holds
        struct.pack(">I4s", 4, b"free"),  # impossible size
    ],
    ids=["size", "type", "size64", "payload", "invalid"],
)
def test_top_level_atoms_reports_a_damaged_atom_in_french_never_struct_error(
    tmp_path: Path, tail: bytes
) -> None:
    with pytest.raises(MediaError, match="atome"):
        top_level_atoms(_write(tmp_path, FTYP, _atom(b"moov", b"m" * 8), tail))
