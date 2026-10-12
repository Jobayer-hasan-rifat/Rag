"""Tests against the real, pinned embedding model.

Skipped unless RAG_REAL_MODEL_TESTS=1, because the first run downloads about 0.5 GB and loading
takes seconds. Run them after changing the model, its revision, the prefixes or the provider:

    RAG_REAL_MODEL_TESTS=1 python -m pytest tests/integration/test_real_embedding_model.py

The semantic assertions use the synthetic evaluation dataset and are deliberately loose: they
guard against a broken model setup (wrong prefixes, wrong pooling, wrong revision), not for
retrieval quality, which is measured by `python -m evaluation.retrieval_eval`.
"""

import os
import time
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embeddings.registry import embedding_config, get_provider, runtime_settings
from app.embeddings.sentence_transformer import SentenceTransformerProvider
from evaluation.dataset import load_dataset
from evaluation.retrieval_eval import evaluate_memory
from tests.conftest import ClientFactory, SettingsFactory
from tests.files import make_user, upload_ok
from tests.processing_helpers import embed_now, process_now

pytestmark = [
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.environ.get("RAG_REAL_MODEL_TESTS") != "1",
        reason="set RAG_REAL_MODEL_TESTS=1 to run tests that load the real embedding model",
    ),
]

REAL: dict[str, Any] = {
    "embedding_provider": "sentence_transformers",
    "embedding_model": "intfloat/multilingual-e5-small",
    "embedding_model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
    "embedding_query_prefix": "query: ",
    "embedding_passage_prefix": "passage: ",
    "embedding_batch_size": 16,
    "embedding_device": "cpu",
}


@pytest.fixture(scope="module")
def provider() -> Iterator[SentenceTransformerProvider]:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost",
        celery_broker_url="redis://localhost",
        celery_result_backend="redis://localhost",
        jwt_secret_key="x" * 40,
        **REAL,
    )
    instance = get_provider(settings)
    assert isinstance(instance, SentenceTransformerProvider)
    instance.warmup()
    yield instance


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b)


def test_the_model_loads_with_the_pinned_revision_and_expected_shape(
    provider: SentenceTransformerProvider,
) -> None:
    assert provider.config.dimension == 384 and provider.device == "cpu"

    vectors = provider.embed_documents(["a short passage", "আরেকটি ছোট অনুচ্ছেদ"]).vectors

    assert vectors.shape == (2, 384) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)


def test_embeddings_are_deterministic_and_batch_independent(
    provider: SentenceTransformerProvider,
) -> None:
    texts = [
        "ইলিশ বাংলাদেশের জাতীয় মাছ",
        "An English sentence about rivers",
        "mixed বাংলা and English",
    ]

    together = provider.embed_documents(texts).vectors
    again = provider.embed_documents(texts).vectors
    alone = np.vstack([provider.embed_documents([t]).vectors for t in texts])

    assert np.allclose(together, again, atol=1e-6)
    assert np.allclose(together, alone, atol=1e-4)  # padding must not change a text's vector


def test_query_and_passage_prefixes_change_the_vectors(
    provider: SentenceTransformerProvider,
) -> None:
    with_prefix = provider.embed_query("river erosion")
    without_prefix = SentenceTransformerProvider(
        provider.config.__class__(**{**provider.config.identity(), "query_prefix": ""}),
        provider._runtime,
    ).embed_query("river erosion")

    assert cosine(with_prefix, without_prefix) < 0.999


@pytest.mark.parametrize(
    ("query", "relevant", "irrelevant"),
    [
        ("ইলিশ ধরা কবে বন্ধ থাকে?", "bn1-2", "en1-1"),
        ("How long can submerged rice plants survive in floodwater?", "en1-2", "bn2-1"),
        ("model কেন নতুন data-তে ভালো করে না?", "mx2-2", "mx3-2"),
        ("What is the national fish of Bangladesh?", "bn1-1", "en2-1"),
    ],
    ids=["bangla", "english", "mixed", "cross-lingual"],
)
def test_the_relevant_passage_scores_above_an_unrelated_one(
    provider: SentenceTransformerProvider, query: str, relevant: str, irrelevant: str
) -> None:
    passages = {p.id: p.text for p in load_dataset().passages}
    vectors = provider.embed_documents([passages[relevant], passages[irrelevant]]).vectors
    q = provider.embed_query(query)

    assert cosine(q, vectors[0]) > cosine(q, vectors[1])


def test_a_thousand_character_chunk_fits_the_models_sequence_limit(
    provider: SentenceTransformerProvider,
) -> None:
    passages = [p.text for p in load_dataset().passages]
    bangla = " ".join(p.text for p in load_dataset().passages if p.language == "bn")[:1000]
    english = " ".join(p.text for p in load_dataset().passages if p.language == "en")[:1000]

    batch = provider.embed_documents([bangla, english, "word " * 2000])

    assert len(bangla) == len(english) == 1000 and len(passages) == 48
    assert batch.truncated == (False, False, True)  # a 1000-character chunk is not truncated


def test_the_model_reloads_offline_from_the_cache(provider: SentenceTransformerProvider) -> None:
    config = provider.config
    offline = SentenceTransformerProvider(
        config, type(provider._runtime)(device="cpu", allow_download=False)
    )

    started = time.monotonic()
    offline.warmup()

    assert time.monotonic() - started < 60 and offline.embed_query("test").shape == (384,)


def test_retrieval_on_the_synthetic_dataset_clears_a_loose_quality_floor(
    provider: SentenceTransformerProvider,
) -> None:
    report = evaluate_memory(provider, load_dataset(), batch_size=16)

    # measured at selection time: Recall@3 = 1.000, Bangla Recall@1 = 0.917 on this dataset
    assert report.overall["recall_at_3"] >= 0.9
    assert report.by_category["bangla"]["recall_at_3"] >= 0.8
    assert report.by_category["mixed"]["recall_at_3"] >= 0.8
    assert (report.answerability_auroc or 0) >= 0.9


def test_real_model_through_the_whole_pipeline_and_the_search_endpoint(
    auth_client_factory: ClientFactory, make_settings: SettingsFactory
) -> None:
    client: TestClient = auth_client_factory(**REAL)
    alice = make_user(client, "alice@example.com")
    corpus = {
        "hilsa.txt": "ইলিশ বাংলাদেশের জাতীয় মাছ। "
        "ডিম ছাড়ার মৌসুমে অক্টোবর মাসে ইলিশ ধরা নিষিদ্ধ থাকে।",
        "rice.txt": "Farmers transplant Aman rice seedlings in July, when monsoon rain "
        "fills the fields.",
        "db.txt": "Query ধীর হলে প্রথমে EXPLAIN চালিয়ে দেখুন, তারপর প্রয়োজন হলে index যোগ করুন।",
    }
    for name, text in corpus.items():
        document = upload_ok(client, alice, filename=name, content=text.encode(), content_type=None)
        assert process_now(client, str(document["id"])).status == "chunked"
        assert embed_now(client, str(document["id"])).status == "ready"

    def top(query: str) -> str:
        response = client.post("/api/v1/search/semantic", headers=alice, json={"query": query})
        assert response.status_code == 200, response.text
        return str(response.json()["data"]["results"][0]["document_name"])

    assert top("ইলিশ ধরা কবে নিষিদ্ধ?") == "hilsa.txt"
    assert top("When do farmers plant rice seedlings?") == "rice.txt"
    assert top("query slow হলে কী করব?") == "db.txt"
    assert embedding_config(client.app.state.settings).model == REAL["embedding_model"]  # type: ignore[attr-defined]
    assert runtime_settings(client.app.state.settings).device == "cpu"  # type: ignore[attr-defined]
