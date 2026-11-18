"""Scene vector index (task 12a): vectors as blobs, free-text search over `scene_vectors`."""

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select
from sqlalchemy.orm import Session

from rushes.db.models import Episode, Scene, SceneVector
from rushes.models.base import ImageEncoder
from rushes.models.clip import MODEL_ID

FLOAT32 = np.dtype("<f4")


def model_id(encoder: ImageEncoder) -> str:
    """The model an encoder reports (`model_name`), else the current CLIP of the manifest."""
    return str(getattr(encoder, "model_name", MODEL_ID))


def vector_blob(v: NDArray[np.float32]) -> bytes:
    """A vector as stored: float32, little endian."""
    return np.ascontiguousarray(v, dtype=FLOAT32).tobytes()


def blob_vector(b: bytes, dim: int) -> NDArray[np.float32]:
    """The vector of `dim` floats stored in `b`; ValueError when the blob has another size."""
    if len(b) != dim * FLOAT32.itemsize:
        raise ValueError(f"dimension {dim} attendue, {len(b)} octets reçus")
    return np.frombuffer(b, dtype="<f4").astype(np.float32)


def _unit(rows: NDArray[np.float32]) -> NDArray[np.float32]:
    norms = np.linalg.norm(rows, axis=-1, keepdims=True)
    return (rows / np.where(norms == 0, 1.0, norms)).astype(np.float32)


def search_scenes(
    session: Session,
    encoder: ImageEncoder,
    query: str,
    *,
    account_id: str | None = None,
    series_id: str | None = None,
    k: int = 5,
) -> list[tuple[int, float]]:
    """The `k` scenes closest to the text `query`: (scene_id, cosine), best first.

    Only vectors of the encoder's model and dimension count (two models are two spaces).
    `account_id` keeps the scenes of the episodes that account owns, `series_id` those of one
    series. Equal cosines: the lowest scene id first. The query is encoded before the first
    read, so the session holds no transaction while the encoder works.

    No provenance filter: task 30 (`compile themes`) must leave out the scenes of `community`
    episodes (R15: they are signals, never material of a post).
    """
    query_vector = _unit(np.asarray(encoder.encode_texts([query]), dtype=np.float32))[0]
    found = select(SceneVector.scene_id, SceneVector.embedding).where(
        SceneVector.dim == encoder.dim, SceneVector.model == model_id(encoder)
    )
    if account_id is not None or series_id is not None:
        found = found.join(Scene, Scene.id == SceneVector.scene_id).join(
            Episode, Episode.id == Scene.episode_id
        )
    if account_id is not None:
        found = found.where(Episode.owner_account_id == account_id)
    if series_id is not None:
        found = found.where(Episode.series_id == series_id)
    rows = session.execute(found.order_by(SceneVector.scene_id)).all()
    if not rows or k <= 0:
        return []
    ids = np.array([row.scene_id for row in rows])
    matrix = _unit(np.stack([blob_vector(row.embedding, encoder.dim) for row in rows]))
    cosines = matrix @ query_vector
    order = np.lexsort((ids, -cosines))[:k]  # best cosine first, then the lowest id
    return [(int(ids[i]), float(cosines[i])) for i in order]
