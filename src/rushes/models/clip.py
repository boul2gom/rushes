"""OpenCLIP ViT-B-32 encoder (R39: local, weights from the manifest, no network).

`open_clip` and `torch` are imported when the encoder is built, never when this module is.

`threads` goes to `torch.set_num_threads`, which is process-wide: it is part of the CPU budget of
the run (the hub keeps the default of 4, the server's `cpus: 4`), not a per-encoder setting.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

MODEL_NAME = "ViT-B-32"
# the weights of manifest.toml (`[[clip]]`, laion/CLIP-ViT-B-32-DataComp.XL-s13B-b90K)
PRETRAINED = "datacomp_xl_s13b_b90k"
MODEL_ID = f"{MODEL_NAME}/{PRETRAINED}"  # written in scene_vectors.model: what was really loaded


class OpenClipEncoder:
    """`ImageEncoder`: L2-normalised 512-dimensional vectors of images and of texts."""

    dim = 512
    model_name = MODEL_ID

    def __init__(self, weights: Path, *, threads: int = 4, batch: int = 32) -> None:
        import open_clip
        import torch

        torch.set_num_threads(threads)
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=str(weights), device="cpu"
        )
        self._torch = torch
        self._model: Any = model.eval()
        self._preprocess: Any = preprocess
        self._tokenize: Any = open_clip.get_tokenizer(MODEL_NAME)
        self._batch = max(batch, 1)

    def _normalized(self, features: Any) -> NDArray[np.float32]:
        features = features / features.norm(dim=-1, keepdim=True)
        return np.ascontiguousarray(features.cpu().numpy(), dtype=np.float32)

    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]:
        out: list[NDArray[np.float32]] = []
        with self._torch.inference_mode():
            for start in range(0, len(images), self._batch):
                tensors = []
                for path in images[start : start + self._batch]:
                    with Image.open(path) as picture:
                        tensors.append(self._preprocess(picture.convert("RGB")))
                out.append(self._normalized(self._model.encode_image(self._torch.stack(tensors))))
        return np.concatenate(out) if out else np.zeros((0, self.dim), dtype=np.float32)

    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        out: list[NDArray[np.float32]] = []
        with self._torch.inference_mode():
            for start in range(0, len(texts), self._batch):
                tokens = self._tokenize(list(texts[start : start + self._batch]))
                out.append(self._normalized(self._model.encode_text(tokens)))
        return np.concatenate(out) if out else np.zeros((0, self.dim), dtype=np.float32)
