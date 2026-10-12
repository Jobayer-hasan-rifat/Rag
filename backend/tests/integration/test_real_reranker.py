"""Tests against the real, pinned cross-encoder.

Skipped unless RAG_REAL_MODEL_TESTS=1, because the first run downloads about 2.3 GB. Run them
after changing the reranker model, its revision or the loader:

    RAG_REAL_MODEL_TESTS=1 python -m pytest tests/integration/test_real_reranker.py

The assertions are deliberately loose: they guard against a broken setup (wrong revision, wrong
output head, scrambled batching), not for retrieval quality, which is measured by
`python -m evaluation.rerank_eval`.
"""

import os
import time
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from app.config import Settings
from app.reranking.cross_encoder import CrossEncoderReranker
from app.reranking.registry import get_reranker
from app.reranking.types import RerankerTimeoutError
from tests.conftest import ClientFactory
from tests.files import make_user
from tests.search_helpers import ready_doc

pytestmark = [
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.environ.get("RAG_REAL_MODEL_TESTS") != "1",
        reason="set RAG_REAL_MODEL_TESTS=1 to run tests that load the real reranker model",
    ),
]

REAL: dict[str, Any] = {
    "rerank_enabled": True,
    "rerank_provider": "cross_encoder",
    "rerank_model": "BAAI/bge-reranker-v2-m3",
    "rerank_model_revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
    "rerank_device": "cpu",
    "rerank_batch_size": 4,
}


@pytest.fixture(scope="module")
def reranker() -> Iterator[CrossEncoderReranker]:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost",
        celery_broker_url="redis://localhost",
        celery_result_backend="redis://localhost",
        jwt_secret_key="x" * 40,
        **REAL,
    )
    instance = get_reranker(settings)
    assert isinstance(instance, CrossEncoderReranker)
    instance.warmup()
    yield instance


def test_a_relevant_english_passage_outscores_an_irrelevant_one(
    reranker: CrossEncoderReranker,
) -> None:
    batch = reranker.score(
        "When are Aman rice seedlings transplanted?",
        [
            "Farmers transplant Aman rice seedlings in July, when monsoon rain fills the fields.",
            "A database index speeds up queries on large tables.",
        ],
    )

    assert batch.scores[0] > batch.scores[1] and np.isfinite(batch.scores).all()


def test_a_relevant_bangla_passage_outscores_an_irrelevant_one(
    reranker: CrossEncoderReranker,
) -> None:
    batch = reranker.score(
        "ইলিশ ধরা কবে বন্ধ থাকে?",
        [
            "বর্ষায় ডিম পাড়ার সময় ইলিশ ধরা কয়েক মাস নিষিদ্ধ থাকে।",
            "ঢাকা থেকে বরিশাল যাওয়ার লঞ্চ রাতে ছাড়ে।",
        ],
    )

    assert batch.scores[0] > batch.scores[1]


def test_the_scores_do_not_depend_on_batching_or_input_order(
    reranker: CrossEncoderReranker,
) -> None:
    query = "How do I rotate a signing key?"
    passages = [f"Passage number {i} about keys and services " * (i + 1) for i in range(7)]
    together = reranker.score(query, passages).scores
    one_by_one = np.array([reranker.score(query, [p]).scores[0] for p in passages])
    reversed_ = reranker.score(query, passages[::-1]).scores[::-1]

    np.testing.assert_allclose(together, one_by_one, atol=1e-3)
    np.testing.assert_allclose(together, reversed_, atol=1e-3)


def test_passages_over_the_length_limit_are_flagged(reranker: CrossEncoderReranker) -> None:
    long_text = "word " * 900

    batch = reranker.score("query", ["short passage", long_text])

    assert batch.truncated == (False, True)


def test_an_expired_deadline_stops_before_any_inference(reranker: CrossEncoderReranker) -> None:
    with pytest.raises(RerankerTimeoutError):
        reranker.score("query", ["a passage"], deadline=time.monotonic() - 1)


def test_empty_input_scores_nothing(reranker: CrossEncoderReranker) -> None:
    batch = reranker.score("query", [])

    assert len(batch.scores) == 0 and batch.truncated == ()


def test_the_loaded_model_is_the_pinned_revision_on_cpu(reranker: CrossEncoderReranker) -> None:
    assert reranker.device == "cpu"
    assert reranker.config.revision == REAL["rerank_model_revision"]


def test_hybrid_search_with_the_real_reranker(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(**REAL)
    alice = make_user(client, "alice@example.com")
    ready_doc(
        client, alice, "Farmers transplant rice seedlings in July after the monsoon.", "r.txt"
    )
    ready_doc(client, alice, "A database index speeds up queries on large tables.", "d.txt")

    response = client.post(
        "/api/v1/search/hybrid",
        headers=alice,
        json={"query": "when do farmers plant rice?", "rerank": True, "top_k": 2},
    )

    data = response.json()["data"]
    assert response.status_code == 200 and data["search"]["rerank"]["applied"] is True
    assert data["results"][0]["document_name"] == "r.txt"
    assert data["results"][0]["rerank_score"] > data["results"][1]["rerank_score"]
