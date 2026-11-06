from pathlib import Path

import pytest

from rushes.paths import PathError, Paths


def test_paths_derive_from_data_dir(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    for p in (
        paths.db,
        paths.cache_models,
        paths.cookies_youtube,
        paths.sources,
        paths.renders,
        paths.inbox,
        paths.outbox,
        paths.outbox_test,
        paths.reports,
        paths.logs,
        paths.locks,
        paths.imports,
        paths.tmp,
    ):
        assert paths.data_dir in p.parents


def test_paths_layout_matches_spec(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    assert paths.db.name == "rushes.db"
    assert paths.cookies_youtube == paths.data_dir / "cookies" / "youtube.txt"
    assert paths.episode_dir("e_000001") == paths.data_dir / "episodes" / "e_000001"


def test_r14_outbox_test_is_distinct_from_outbox(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    assert paths.outbox != paths.outbox_test
    assert paths.outbox not in paths.outbox_test.parents
    assert paths.outbox_test not in paths.outbox.parents


def test_env_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHES_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("RUSHES_DATA_DIR", str(tmp_path / "dat"))
    paths = Paths.from_env()
    assert paths.config_dir == (tmp_path / "cfg").resolve()
    assert paths.data_dir == (tmp_path / "dat").resolve()


def test_defaults_are_relative_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    paths = Paths.from_env()
    assert paths.config_dir == (tmp_path / "config").resolve()
    assert paths.data_dir == (tmp_path / "data").resolve()


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "/abs"])
def test_identifier_escaping_data_dir_is_refused(tmp_path: Path, bad: str) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    with pytest.raises(PathError):
        paths.episode_dir(bad)


def test_outbox_post_dir_uses_identifiers_only(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    post_dir = paths.post_dir("st_tt", "p_000123")
    assert post_dir == paths.outbox / "st_tt" / "p_000123"
    assert paths.post_dir("st_tt", "p_000123", test=True) == (
        paths.outbox_test / "st_tt" / "p_000123"
    )


def test_paths_runtime_folders(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    assert paths.logs == paths.data_dir / "logs"
    assert paths.locks == paths.data_dir / "locks"
    assert paths.imports == paths.data_dir / "import"
    assert paths.tmp == paths.data_dir / "tmp"
