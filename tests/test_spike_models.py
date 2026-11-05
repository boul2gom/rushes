"""Pure helpers of scripts/spike_models.py (dev-only feasibility spike); no network, no models."""

import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spike_models.py"
KEYS = {"name", "ok", "load_s", "infer_s", "peak_rss_mb", "sha256", "bytes", "error"}


@pytest.fixture
def spike(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("spike_models", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "spike_models", module)
    spec.loader.exec_module(module)
    return module


def test_spike_line_has_exactly_the_documented_keys(spike: ModuleType) -> None:
    line = spike.to_line(spike.record("silero", ok=True, load_s=0.5, infer_s=0.25))
    assert "\n" not in line
    data = json.loads(line)
    assert set(data) == KEYS
    assert data["name"] == "silero"
    assert data["ok"] is True
    assert data["error"] is None


def test_spike_record_defaults_to_failure_without_measures(spike: ModuleType) -> None:
    data = spike.record("clip", error="boom")
    assert data["ok"] is False
    assert data["peak_rss_mb"] is None
    assert data["error"] == "boom"


def test_spike_rss_is_bytes_on_macos_and_kib_on_linux(spike: ModuleType) -> None:
    assert spike.rss_mb(512 * 1024 * 1024, "darwin") == pytest.approx(512.0)
    assert spike.rss_mb(512 * 1024, "linux") == pytest.approx(512.0)


def test_spike_sha256_streams_the_file(spike: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    payload = b"abc" * 1_000_000
    path.write_bytes(payload)
    assert spike.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_spike_parent_keeps_the_last_json_line_of_the_child(spike: ModuleType) -> None:
    out = 'warning: noise\n{"name": "a"}\nmore noise\n{"name": "b", "ok": true}\ntrailing\n'
    assert spike.last_json_line(out) == {"name": "b", "ok": True}
    assert spike.last_json_line("no json here\n") is None
    assert spike.last_json_line("") is None


def test_spike_hooks_validation_follows_the_schema(spike: ModuleType) -> None:
    good = json.dumps({"hooks": [{"text": "Wait for it", "type": "curiosity"}]})
    assert spike.hooks_valid(good)
    assert not spike.hooks_valid("not json")
    assert not spike.hooks_valid(json.dumps({"hooks": [{"text": "x"}]}))
    assert not spike.hooks_valid(json.dumps({"other": []}))


def test_spike_unit_norm_check_accepts_only_512_unit_vectors(spike: ModuleType) -> None:
    unit = [1.0] + [0.0] * 511
    assert spike.unit_vectors_ok([unit, unit], dim=512)
    assert not spike.unit_vectors_ok([[2.0] + [0.0] * 511], dim=512)
    assert not spike.unit_vectors_ok([[1.0, 0.0]], dim=512)


def test_spike_model_registry_covers_the_ten_documented_names(spike: ModuleType) -> None:
    expected = {
        "silero",
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
    assert set(spike.MODELS) == expected


def test_spike_hooks_schema_requires_at_least_one_bounded_hook(spike: ModuleType) -> None:
    hooks = spike.HOOKS_SCHEMA["properties"]["hooks"]
    assert hooks["minItems"] == 1
    assert hooks["maxItems"] >= 1
    assert "maxLength" in hooks["items"]["properties"]["text"]
    assert not spike.hooks_valid(json.dumps({"hooks": []}))


def test_spike_clip_checks_the_models_own_normalised_output(spike: ModuleType) -> None:
    import inspect

    source = inspect.getsource(spike.infer_clip)
    assert source.count("normalize=True") == 2
    assert "functional.normalize" not in source


def test_spike_yunet_requires_the_contrib_modules(spike: ModuleType) -> None:
    spike.require_contrib(SimpleNamespace(saliency=object()))
    with pytest.raises(RuntimeError, match="contrib"):
        spike.require_contrib(SimpleNamespace())


def _make_archive(path: Path, inner: str) -> None:
    src = path.parent / "src" / inner
    src.mkdir(parents=True)
    (src / "model.onnx").write_bytes(b"weights")
    with tarfile.open(path, "w:bz2") as tar:
        tar.add(src, arcname=inner)


def test_spike_extraction_publishes_the_directory_atomically(
    spike: ModuleType, tmp_path: Path
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    archive = tmp_path / "m.tar.bz2"
    _make_archive(archive, "m")
    root = spike.extract_archive(archive, models, "m")
    assert (root / "model.onnx").read_bytes() == b"weights"
    assert [p.name for p in models.iterdir()] == ["m"]  # no temp directory left behind


def test_spike_extraction_of_a_broken_archive_leaves_no_root(
    spike: ModuleType, tmp_path: Path
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    archive = tmp_path / "m.tar.bz2"
    archive.write_bytes(b"not a tarball")
    with pytest.raises((tarfile.TarError, OSError)):
        spike.extract_archive(archive, models, "m")
    assert list(models.iterdir()) == []


def test_spike_extraction_ignores_a_stale_partial_temp_directory(
    spike: ModuleType, tmp_path: Path
) -> None:
    models = tmp_path / "models"
    (models / ".extract-stale" / "m").mkdir(parents=True)  # crashed earlier run
    archive = tmp_path / "m.tar.bz2"
    _make_archive(archive, "m")
    root = spike.extract_archive(archive, models, "m")
    assert (root / "model.onnx").exists()


def test_spike_child_timeout_is_reported_as_a_failed_record(spike: ModuleType) -> None:
    cmd = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    rec = spike.run_command("slow", cmd, timeout_s=1.0)
    assert rec["ok"] is False
    assert rec["error"] == "timeout"
    assert set(rec) == KEYS


def test_spike_child_result_gets_a_peak_rss(spike: ModuleType) -> None:
    line = json.dumps(spike.record("fast", ok=True))
    cmd = [sys.executable, "-I", "-c", f"print({line!r})"]
    rec = spike.run_command("fast", cmd, timeout_s=30.0)
    assert rec["ok"] is True
    assert rec["peak_rss_mb"] > 0
