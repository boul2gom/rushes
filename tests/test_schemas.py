import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from rushes.schemas import CleanFile, read_json, write_json


def _clean() -> CleanFile:
    return CleanFile(
        version=1,
        episode_id=7,
        source_duration_ms=60_000,
        kept=(3000, 60_000),
        removed=[(0, 3000, "black")],
    )


def test_write_json_roundtrip_is_utf8_indent_1_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "episodes" / "e_000007" / "clean.json"
    write_json(_clean(), path)
    assert read_json(CleanFile, path) == _clean()
    text = path.read_text(encoding="utf-8")
    assert text.startswith('{\n "version": 1')
    assert [p.name for p in path.parent.iterdir()] == ["clean.json"]  # no temporary file left


def test_write_json_twice_gives_the_same_bytes(tmp_path: Path) -> None:
    path = tmp_path / "clean.json"
    write_json(_clean(), path)
    first = path.read_bytes()
    write_json(_clean(), path)
    assert path.read_bytes() == first


def test_clean_file_forbids_unknown_fields_versions_and_kinds() -> None:
    good = _clean().model_dump()
    with pytest.raises(ValidationError):
        CleanFile.model_validate({**good, "extra": 1})
    with pytest.raises(ValidationError):
        CleanFile.model_validate({**good, "version": 2})
    with pytest.raises(ValidationError):
        CleanFile.model_validate({**good, "removed": [(0, 1, "logo")]})


def test_clean_file_span_is_optional() -> None:
    assert _clean().span is None


def _loaded_after(statement: str) -> set[str]:
    """Modules in sys.modules after `statement`, in a clean interpreter (-I: no env, no cwd)."""
    code = f"{statement}; import sys; print('\\n'.join(sorted(sys.modules)))"
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    return set(done.stdout.split())


@pytest.mark.parametrize("statement", ["import rushes.schemas", "import rushes.package.upload"])
def test_light_modules_do_not_load_the_config_or_the_console(statement: str) -> None:
    """`rushes serve` (task 36) must start fast: schemas and build_upload stay light."""
    loaded = _loaded_after(statement)
    assert not {"rushes.config", "rushes.ui", "rich", "yaml"} & loaded
