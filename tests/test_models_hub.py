import subprocess
import sys
import weakref
from pathlib import Path
from typing import Any

import pytest

from fakes.models import FakeVad
from helpers import FakeStep
from rushes.config import load_config
from rushes.context import Context, Filters
from rushes.errors import RushesError
from rushes.models import hub as hub_module
from rushes.models.hub import ModelHub
from rushes.paths import Paths
from rushes.runner import run_step


class Dummy:
    """Something a weak reference can point at."""


def _hub(tmp_path: Path, config_dir: Path, **factories: Any) -> ModelHub:
    paths = Paths(config_dir=config_dir, data_dir=tmp_path / "data")
    return ModelHub(paths, load_config(config_dir).settings, factories=factories)


def test_r19_models_loaded_once_per_step(ctx: Context) -> None:
    step = FakeStep(on_run=lambda _item: ctx.models.vad())
    assert run_step(ctx, step, Filters()).done == 3
    assert ctx.models.loads()["vad"] == 1


def test_r19_one_heavy_model_at_a_time(tmp_path: Path, config_dir: Path) -> None:
    seen: dict[str, bool] = {}
    refs: list[weakref.ref[Dummy]] = []

    def make_transcriber() -> Dummy:
        seen["clip_dead_while_loading"] = refs[0]() is None
        return Dummy()

    hub = _hub(tmp_path, config_dir, clip=Dummy, transcriber=make_transcriber)
    refs.append(weakref.ref(hub.clip()))  # type: ignore[arg-type]
    assert refs[0]() is not None
    hub.transcriber()
    assert seen == {"clip_dead_while_loading": True}  # never two heavy models at once
    assert hub.loads() == {"clip": 1, "transcriber": 1}


def test_heavy_model_is_cached_until_another_one_replaces_it(
    tmp_path: Path, config_dir: Path
) -> None:
    hub = _hub(tmp_path, config_dir, clip=Dummy, transcriber=Dummy)
    assert hub.clip() is hub.clip()
    assert hub.loads() == {"clip": 1}
    hub.transcriber()
    hub.clip()
    assert hub.loads() == {"clip": 2, "transcriber": 1}


def test_light_models_stay_loaded_when_a_heavy_one_is_swapped(
    tmp_path: Path, config_dir: Path
) -> None:
    hub = _hub(tmp_path, config_dir, vad=Dummy, clip=Dummy, transcriber=Dummy)
    vad = hub.vad()
    hub.clip()
    hub.transcriber()
    assert hub.vad() is vad and hub.loads()["vad"] == 1


def test_release_drops_heavy_model(tmp_path: Path, config_dir: Path) -> None:
    hub = _hub(tmp_path, config_dir, clip=Dummy)
    ref = weakref.ref(hub.clip())  # type: ignore[arg-type]
    hub.release()
    assert ref() is None
    hub.clip()
    assert hub.loads() == {"clip": 2}  # reloaded after the release


@pytest.mark.parametrize(
    ("accessor", "task"),
    [
        ("transcriber", "12c"),
        ("sounds", "12b"),
        ("faces", "34"),
        ("llm", "14"),
        ("tts", "35"),
    ],
)
def test_models_without_a_factory_name_their_task(
    tmp_path: Path, config_dir: Path, accessor: str, task: str
) -> None:
    hub = _hub(tmp_path, config_dir)
    with pytest.raises(NotImplementedError, match=f"tâche {task}"):
        getattr(hub, accessor)()
    assert hub.loads() == {}


def test_vad_defaults_to_silero_without_importing_it_early(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(hub_module.DEFAULT_FACTORIES, "vad", FakeVad)
    hub = _hub(tmp_path, config_dir)
    assert isinstance(hub.vad(), FakeVad)
    assert hub.loads() == {"vad": 1}


def test_clip_defaults_to_open_clip_on_the_manifest_weights(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class Recorder:
        def __init__(self, weights: Path, *, threads: int = 4, batch: int = 32) -> None:
            seen.update(weights=weights, batch=batch)

    monkeypatch.setattr("rushes.models.clip.OpenClipEncoder", Recorder)
    hub = _hub(tmp_path, config_dir)
    weights = hub.paths.cache_models / "clip" / "open_clip_model.safetensors"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"weights")
    assert isinstance(hub.clip(), Recorder)
    assert seen == {"weights": weights, "batch": hub.settings.analyze.clip_batch}
    assert hub.loads() == {"clip": 1}


def test_clip_without_its_weights_says_how_to_fetch_them(tmp_path: Path, config_dir: Path) -> None:
    hub = _hub(tmp_path, config_dir)
    with pytest.raises(RushesError, match="clip") as caught:
        hub.clip()
    assert "models fetch --only clip" in (caught.value.hint or "")
    assert hub.loads() == {}


def test_a_given_clip_factory_wins_over_the_default(tmp_path: Path, config_dir: Path) -> None:
    hub = _hub(tmp_path, config_dir, clip=Dummy)
    assert isinstance(hub.clip(), Dummy)  # no weights needed


def test_importing_the_hub_does_not_import_torch() -> None:
    code = (
        "import sys, rushes.cli, rushes.context, rushes.models.hub, rushes.models.clip, "
        "rushes.steps; "
        "sys.exit(any(m in sys.modules for m in "
        "('torch', 'onnxruntime', 'silero_vad', 'open_clip')))"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], check=False, capture_output=True)
    assert done.returncode == 0, done.stderr.decode()


def test_clip_refuses_a_pretrained_name_that_is_not_the_manifest_weights(
    tmp_path: Path, config_dir: Path
) -> None:
    """The `model` column must name what is loaded: the weights are the manifest's only `clip`."""
    hub = _hub(tmp_path, config_dir)
    # the config refuses it at load: update the settings unvalidated to reach the guard
    models = hub.settings.models.model_copy(update={"clip_pretrained": "other"})
    hub.settings = hub.settings.model_copy(update={"models": models})
    weights = hub.paths.cache_models / "clip" / "open_clip_model.safetensors"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"weights")
    with pytest.raises(RushesError, match="clip_pretrained") as caught:
        hub.clip()
    assert "datacomp_xl_s13b_b90k" in (caught.value.hint or "")
    assert hub.loads() == {}


def test_open_clip_encoder_names_the_model_it_loads() -> None:
    from rushes.models.clip import MODEL_ID, OpenClipEncoder

    assert OpenClipEncoder.model_name == MODEL_ID == "ViT-B-32/datacomp_xl_s13b_b90k"
    assert OpenClipEncoder.dim == 512
