"""Fetch the configured reranker model into the cache and check that it loads.

    python -m app.reranking.download

Uses the application's RERANK_* settings and the same safety rules (pinned revision,
safetensors only, no remote code). Exit status 0 means the model is cached and usable.
"""

import sys
import time

from app.config import get_settings
from app.reranking.registry import get_reranker
from app.reranking.types import RerankerError


def main() -> int:
    settings = get_settings().model_copy(update={"rerank_allow_download": True})
    print(f"model {settings.rerank_model} @ {settings.rerank_model_revision}", flush=True)
    started = time.monotonic()
    try:
        reranker = get_reranker(settings)
        reranker.warmup()
        probe = reranker.score("test", ["a test passage"])
    except RerankerError as error:
        print(f"FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    seconds = time.monotonic() - started
    print(
        f"ready: loaded on {reranker.device} in {seconds:.1f} s; probe score {probe.scores[0]:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
