import numpy as np

from app.embeddings.types import EmbeddingConfig, InvalidEmbeddingError, Vectors

_UNIT_TOLERANCE = 1e-2
_MIN_NORM = 1e-6


def validate_vectors(vectors: Vectors, config: EmbeddingConfig, *, expected: int) -> Vectors:
    """Reject anything that must not reach the database or a similarity query.

    Wrong dimensions, non-finite values and zero vectors are provider bugs or model mismatches;
    storing them would silently corrupt search, so they are errors, never "repaired".
    """
    array = np.asarray(vectors)
    if array.ndim != 2 or array.shape[0] != expected:
        raise InvalidEmbeddingError(f"expected {expected} vectors, got shape {tuple(array.shape)}")
    if array.shape[1] != config.dimension:
        raise InvalidEmbeddingError(
            f"model returned dimension {array.shape[1]}, configured dimension is {config.dimension}"
        )
    if not np.isfinite(array).all():
        raise InvalidEmbeddingError("embedding contains NaN or infinite values")
    norms = np.linalg.norm(array, axis=1)
    if (norms < _MIN_NORM).any():
        raise InvalidEmbeddingError("embedding is a zero vector")
    if config.normalized and (np.abs(norms - 1.0) > _UNIT_TOLERANCE).any():
        raise InvalidEmbeddingError("embedding is not unit length although normalisation is on")
    return array.astype(np.float32, copy=False)
