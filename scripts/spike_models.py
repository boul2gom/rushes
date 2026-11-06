"""Feasibility spike (task 42, dev only, network allowed): one inference per model.

The parent runs each model in its own process (`--one NAME`), reads the child's peak RSS
and writes one JSON line per model to `--out`. Models land in `<RUSHES_DATA_DIR>/cache/models/`.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from rushes.paths import Paths

SAMPLE_RATE = 16_000
THREADS = 4
CHILD_TIMEOUT_S = 1800  # per model, downloads included
GITHUB_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
YUNET_URL = (  # opencv_zoo commit eb9f4f2 (LFS file, hence media.githubusercontent.com)
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "eb9f4f2d4b79b58e5513b5d25d5f0243a8357bf4/models/face_detection_yunet/"
    "face_detection_yunet_2026may.onnx"
)
HOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,  # bounded so a small model cannot ramble until max_tokens
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 80},
                    "type": {"type": "string", "maxLength": 20},
                },
                "required": ["text", "type"],
            },
        }
    },
    "required": ["hooks"],
}

# --- pure helpers (unit-tested) ------------------------------------------------------------


def record(
    name: str,
    *,
    ok: bool = False,
    load_s: float | None = None,
    infer_s: float | None = None,
    peak_rss_mb: float | None = None,
    sha256: str | None = None,
    nbytes: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "load_s": load_s,
        "infer_s": infer_s,
        "peak_rss_mb": peak_rss_mb,
        "sha256": sha256,
        "bytes": nbytes,
        "error": error,
    }


def to_line(rec: dict[str, Any]) -> str:
    return json.dumps(rec, ensure_ascii=False)


def rss_mb(raw: int, platform: str = sys.platform) -> float:
    """ru_maxrss is in bytes on macOS and in KiB on Linux."""
    return raw / (1024 * 1024) if platform == "darwin" else raw / 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def hooks_valid(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return (
        isinstance(hooks, list)
        and len(hooks) >= 1
        and all(
            isinstance(h, dict)
            and isinstance(h.get("text"), str)
            and isinstance(h.get("type"), str)
            for h in hooks
        )
    )


def require_contrib(cv2_module: Any) -> None:
    """Fail if the cv2 in use lacks the contrib modules (saliency, optical flow helpers)."""
    if not hasattr(cv2_module, "saliency"):
        raise RuntimeError("cv2 has no contrib modules (opencv-contrib-python-headless expected)")


def unit_vectors_ok(vectors: Sequence[Sequence[float]], dim: int, tol: float = 1e-3) -> bool:
    return all(
        len(v) == dim and abs(math.sqrt(sum(x * x for x in v)) - 1.0) <= tol for v in vectors
    )


# --- fetching ------------------------------------------------------------------------------


class Fetched(NamedTuple):
    artifact: Path  # file hashed for the manifest (weights file or archive)
    root: Path  # what the loader needs (file or extracted directory)


def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        part.rename(dest)
    return dest


def fetch_archive(url: str, inner: str) -> Callable[[Path], Fetched]:
    """GitHub release `.tar.bz2`; `inner` is the extracted directory name."""

    def fetch(models: Path) -> Fetched:
        archive = download(url, models / url.rsplit("/", 1)[-1])
        return Fetched(archive, extract_archive(archive, models, inner))

    return fetch


def extract_archive(archive: Path, models: Path, inner: str) -> Path:
    """Extract into a temp dir, then rename: `models/inner` only exists once complete."""
    root = models / inner
    if root.exists():
        return root
    tmp = Path(tempfile.mkdtemp(prefix=".extract-", dir=models))
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(tmp, filter="data")
        (tmp / inner).rename(root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return root


def fetch_hf_file(repo: str, revision: str, filename: str) -> Callable[[Path], Fetched]:
    def fetch(models: Path) -> Fetched:
        from huggingface_hub import hf_hub_download

        local = models / repo.replace("/", "__")
        path = Path(hf_hub_download(repo, filename, revision=revision, local_dir=local))
        return Fetched(path, path)

    return fetch


def fetch_hf_model_dir(repo: str, revision: str, weights: str) -> Callable[[Path], Fetched]:
    def fetch(models: Path) -> Fetched:
        from huggingface_hub import snapshot_download

        local = Path(
            snapshot_download(repo, revision=revision, local_dir=models / repo.replace("/", "__"))
        )
        return Fetched(local / weights, local)

    return fetch


def fetch_silero(models: Path) -> Fetched:
    import silero_vad

    weights = Path(silero_vad.__file__).parent / "data" / "silero_vad.onnx"
    return Fetched(weights, weights)


def fetch_yunet(models: Path) -> Fetched:
    path = download(YUNET_URL, models / "face_detection_yunet_2026may.onnx")
    return Fetched(path, path)


# --- load / infer, one pair per model ------------------------------------------------------


def noise(seconds: float = 10.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (0.1 * rng.standard_normal(int(seconds * SAMPLE_RATE))).astype(np.float32)


def sine(seconds: float = 10.0, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def load_silero(root: Path) -> Any:
    from silero_vad import load_silero_vad

    return load_silero_vad(onnx=True)


def infer_silero(model: Any) -> None:
    import torch
    from silero_vad import get_speech_timestamps

    get_speech_timestamps(torch.from_numpy(noise()), model, sampling_rate=SAMPLE_RATE)


def load_whisper(root: Path) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(str(root), device="cpu", compute_type="int8", cpu_threads=THREADS)


def infer_whisper(model: Any) -> None:
    segments, _ = model.transcribe(sine(), language="en", vad_filter=False, word_timestamps=True)
    list(segments)  # the generator only runs when consumed


def load_clip(root: Path) -> Any:
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained=str(root))
    return model.eval(), preprocess, open_clip.get_tokenizer("ViT-B-32")


def infer_clip(handle: Any) -> None:
    import torch
    from PIL import Image

    model, preprocess, tokenizer = handle
    images = [Image.new("RGB", (224, 224), color) for color in ("red", "blue")]
    texts = ["a cartoon character falling down", "a crowd cheering in a stadium"]
    with torch.no_grad():
        image_vecs = model.encode_image(
            torch.stack([preprocess(i) for i in images]), normalize=True
        )
        text_vecs = model.encode_text(tokenizer(texts), normalize=True)
    for vecs in (image_vecs, text_vecs):
        if not unit_vectors_ok(vecs.tolist(), dim=512):
            raise ValueError("CLIP vectors are not 512-d unit vectors")
    # torchvision comes from PyPI on Linux (the CPU index only serves torch): prove it works
    import torchvision

    boxes = torch.tensor([[0.0, 0.0, 10.0, 10.0]])
    torchvision.ops.nms(boxes, torch.tensor([0.9]), 0.5)


def load_ced(root: Path) -> Any:
    import sherpa_onnx as so

    model = so.AudioTaggingModelConfig(ced=str(root / "model.int8.onnx"), num_threads=THREADS)
    return so.AudioTagging(
        so.AudioTaggingConfig(model=model, labels=str(root / "class_labels_indices.csv"), top_k=3)
    )


def infer_ced(tagger: Any) -> None:
    stream = tagger.create_stream()
    stream.accept_waveform(SAMPLE_RATE, noise())
    if not tagger.compute(stream):
        raise ValueError("no label returned")


def load_yunet(root: Path) -> Any:
    import cv2

    require_contrib(cv2)
    return cv2.FaceDetectorYN.create(str(root), "", (640, 360))


def infer_yunet(detector: Any) -> None:
    _, faces = detector.detect(np.zeros((360, 640, 3), np.uint8))
    if faces is not None and len(faces) > 0:
        raise ValueError(f"{len(faces)} face(s) found in a black image")


def load_llm(root: Path) -> Any:
    from llama_cpp import Llama

    return Llama(model_path=str(root), n_ctx=2048, n_threads=THREADS, verbose=False)


def infer_llm(llm: Any) -> None:
    out = llm.create_chat_completion(
        messages=[
            {"role": "user", "content": "Write 2 hooks of under 10 words for a funny cartoon clip."}
        ],
        response_format={"type": "json_object", "schema": HOOKS_SCHEMA},
        max_tokens=200,
        temperature=0.0,
    )
    if not hooks_valid(out["choices"][0]["message"]["content"]):
        raise ValueError("output does not follow the hooks schema")


def check_audio(audio: Any) -> None:
    seconds = len(audio.samples) / audio.sample_rate if audio.sample_rate else 0.0
    if seconds <= 0.5:
        raise ValueError(f"audio too short: {seconds:.2f}s")


def load_supertonic(root: Path) -> Any:
    import sherpa_onnx as so

    files = {
        "duration_predictor": "duration_predictor.int8.onnx",
        "text_encoder": "text_encoder.int8.onnx",
        "vector_estimator": "vector_estimator.int8.onnx",
        "vocoder": "vocoder.int8.onnx",
        "tts_json": "tts.json",
        "unicode_indexer": "unicode_indexer.bin",
        "voice_style": "voice.bin",
    }
    cfg = so.OfflineTtsSupertonicModelConfig(**{k: str(root / v) for k, v in files.items()})
    return so.OfflineTts(
        so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(supertonic=cfg, num_threads=THREADS))
    )


def infer_supertonic(tts: Any) -> None:
    import sherpa_onnx as so

    for lang, text in (
        ("fr", "Bonjour, ceci est un essai."),
        ("en", "Hello, this is a short test of the voice."),
    ):
        gen = so.GenerationConfig()
        gen.extra = {"lang": lang}
        gen.num_steps = 5
        check_audio(tts.generate(text, gen))


def load_kokoro(root: Path) -> Any:
    import sherpa_onnx as so

    cfg = so.OfflineTtsKokoroModelConfig(
        model=str(root / "model.int8.onnx"),
        voices=str(root / "voices.bin"),
        tokens=str(root / "tokens.txt"),
        lexicon=str(root / "lexicon-us-en.txt"),  # sherpa-onnx 2.x needs a lexicon
        data_dir=str(root / "espeak-ng-data"),
        dict_dir=str(root / "dict"),
        lang="en-us",
    )
    return so.OfflineTts(
        so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(kokoro=cfg, num_threads=THREADS))
    )


def infer_kokoro(tts: Any) -> None:
    check_audio(tts.generate("Hello, this is a short test of the voice.", sid=0, speed=1.0))


class Spec(NamedTuple):
    fetch: Callable[[Path], Fetched]
    load: Callable[[Path], Any]
    infer: Callable[[Any], None]


# Revisions and release tags are pinned (read on 2026-11-04); no floating "main".
MODELS: dict[str, Spec] = {
    "silero": Spec(fetch_silero, load_silero, infer_silero),
    "whisper_small": Spec(
        fetch_hf_model_dir(
            "Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120", "model.bin"
        ),
        load_whisper,
        infer_whisper,
    ),
    "whisper_base": Spec(
        fetch_hf_model_dir(
            "Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", "model.bin"
        ),
        load_whisper,
        infer_whisper,
    ),
    "clip": Spec(
        fetch_hf_file(
            "laion/CLIP-ViT-B-32-DataComp.XL-s13B-b90K",
            "f0e2ffa09cbadab3db6a261ec1ec56407ce42912",
            "open_clip_model.safetensors",
        ),
        load_clip,
        infer_clip,
    ),
    "ced": Spec(
        fetch_archive(
            f"{GITHUB_RELEASES}/audio-tagging-models/"
            "sherpa-onnx-ced-mini-audio-tagging-2024-04-19.tar.bz2",
            "sherpa-onnx-ced-mini-audio-tagging-2024-04-19",
        ),
        load_ced,
        infer_ced,
    ),
    "yunet": Spec(fetch_yunet, load_yunet, infer_yunet),
    "qwen4b": Spec(
        fetch_hf_file(
            "unsloth/Qwen3-4B-Instruct-2507-GGUF",
            "a06e946bb6b655725eafa393f4a9745d460374c9",
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        ),
        load_llm,
        infer_llm,
    ),
    "qwen1_7b": Spec(
        fetch_hf_file(
            "unsloth/Qwen3-1.7B-GGUF",
            "d7f544eead698dbd1f15126ef60b45a1e1933222",
            "Qwen3-1.7B-Q4_K_M.gguf",
        ),
        load_llm,
        infer_llm,
    ),
    "supertonic": Spec(
        fetch_archive(
            f"{GITHUB_RELEASES}/tts-models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2",
            "sherpa-onnx-supertonic-3-tts-int8-2026-05-11",
        ),
        load_supertonic,
        infer_supertonic,
    ),
    "kokoro": Spec(
        fetch_archive(
            f"{GITHUB_RELEASES}/tts-models/kokoro-int8-multi-lang-v1_0.tar.bz2",
            "kokoro-int8-multi-lang-v1_0",
        ),
        load_kokoro,
        infer_kokoro,
    ),
}

# --- runner --------------------------------------------------------------------------------


def run_one(name: str, models: Path) -> dict[str, Any]:
    """Fetch, hash, load and run one model; never raises."""
    spec = MODELS[name]
    rec = record(name)
    try:
        fetched = spec.fetch(models)
        rec["sha256"] = sha256_file(fetched.artifact)
        rec["bytes"] = fetched.artifact.stat().st_size
        start = time.perf_counter()
        handle = spec.load(fetched.root)
        rec["load_s"] = round(time.perf_counter() - start, 3)
        start = time.perf_counter()
        spec.infer(handle)
        rec["infer_s"] = round(time.perf_counter() - start, 3)
        rec["ok"] = True
    except Exception as exc:  # the spike reports every failure instead of stopping
        rec["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return rec


def run_command(name: str, cmd: list[str], timeout_s: float) -> dict[str, Any]:
    """Run a child, kill it after `timeout_s`, and add its peak RSS to its JSON line."""
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout_s, kill)
        timer.start()
        try:
            stdout = proc.stdout.read()
            # wait4 gives this child's own ru_maxrss; getrusage(RUSAGE_CHILDREN) is a running
            # maximum over all children so far and could not tell the models apart.
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
        proc.returncode = os.waitstatus_to_exitcode(status)
    if timed_out.is_set():
        rec = record(name, error="timeout")
    else:
        rec = last_json_line(stdout) or record(
            name, error=f"no result (exit code {proc.returncode})"
        )
    rec["peak_rss_mb"] = round(rss_mb(usage.ru_maxrss), 1)
    return rec


def run_child(name: str) -> dict[str, Any]:
    """Run one model in a fresh process."""
    cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "--one", name]
    return run_command(name, cmd, CHILD_TIMEOUT_S)


def main(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    failed = 0
    with out.open("w", encoding="utf-8") as sink:
        for name in MODELS:
            rec = run_child(name)
            line = to_line(rec)
            sink.write(line + "\n")
            sink.flush()
            print(line, flush=True)
            failed += not rec["ok"]
    return 1 if failed else 0


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="JSONL file for the parent run")
    parser.add_argument("--one", choices=sorted(MODELS), help="internal: run a single model")
    args = parser.parse_args(argv)
    if args.one:
        print(to_line(run_one(args.one, Paths.from_env().cache_models)), flush=True)
        return 0
    if args.out is None:
        parser.error("--out is required")
    return main(args.out)


if __name__ == "__main__":
    sys.exit(cli())
