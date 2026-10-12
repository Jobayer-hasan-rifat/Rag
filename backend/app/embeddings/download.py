"""Fetch the configured embedding model into the cache and check that it loads.

    python -m app.embeddings.download

Run this once per deployment (the Compose embedding worker does it on start-up) so the first
embedding job and the first search do not have to download the model. It uses the same
configuration as the application (EMBEDDING_* settings) and the same safety rules: the pinned
revision, safetensors only, no remote code. Exit status 0 means the model is cached and loadable.
"""

import sys
import time

from app.config import get_settings
from app.embeddings.registry import embedding_config, get_provider
from app.embeddings.types import EmbeddingError


def main() -> int:
    settings = get_settings().model_copy(update={"embedding_allow_download": True})
    config = embedding_config(settings)
    print(f"model {config.model} @ {config.revision} ({config.dimension} dimensions)", flush=True)
    started = time.monotonic()
    try:
        provider = get_provider(settings)
        provider.warmup()
        probe = provider.embed_query("test")
    except EmbeddingError as error:
        print(f"FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    seconds = time.monotonic() - started
    device = getattr(provider, "device", "n/a")
    print(
        f"ready: loaded on {device} in {seconds:.1f} s; probe vector has {probe.shape[0]} values",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
