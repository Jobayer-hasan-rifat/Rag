import sys
import types
import uuid
from typing import Any

import numpy as np
import pytest

from app.config import Settings
from app.embeddings import registry
from app.embeddings.constants import VECTOR_DIMENSION
from app.embeddings.inputs import passage_input
from app.embeddings.registry import (
    available_providers,
    embedding_config,
    get_provider,
    register_provider,
    reset_provider_cache,
    runtime_settings,
)
from app.embeddings.sentence_transformer import RuntimeSettings, SentenceTransformerProvider
from app.embeddings.types import (
    PREPROCESSING_VERSION,
    EmbeddingBatch,
    EmbeddingConfig,
    EmbeddingFailedError,
    InvalidEmbeddingError,
    ModelUnavailableError,
)
from app.embeddings.validation import validate_vectors
from tests.conftest import SettingsFactory
from tests.embedding_fakes import ControlledProvider, HashProvider, hashed_vector

REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


def make_config(**overrides: Any) -> EmbeddingConfig:
    values: dict[str, Any] = {
        "provider": "test_hash",
        "model": "org/model",
        "revision": REVISION,
        "dimension": VECTOR_DIMENSION,
    }
    values.update(overrides)
    return EmbeddingConfig(**values)


# --- configuration identity --------------------------------------------------------------


def test_fingerprint_is_stable_and_covers_everything_that_changes_vectors() -> None:
    base = make_config()

    assert base.fingerprint() == make_config().fingerprint()
    assert len(base.fingerprint()) == 64
    changes = {
        "provider": "other",
        "model": "org/other",
        "revision": "f" * 40,
        "dimension": 768,
        "normalized": False,
        "query_prefix": "q: ",
        "passage_prefix": "p: ",
        "max_seq_length": 256,
        "include_heading": True,
        "preprocessing_version": "e2",
    }
    prints = {base.fingerprint()}
    for name, value in changes.items():
        changed = make_config(**{name: value}).fingerprint()
        assert changed not in prints, name
        prints.add(changed)


def test_the_preprocessing_version_is_part_of_the_identity() -> None:
    assert make_config().identity()["preprocessing_version"] == PREPROCESSING_VERSION


def test_config_from_settings_maps_every_field(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        embedding_provider="test_hash",
        embedding_model="org/m",
        embedding_model_revision="abc",
        embedding_query_prefix="q: ",
        embedding_passage_prefix="p: ",
        embedding_max_seq_length=256,
        embedding_include_heading=True,
        embedding_normalize=True,
    )

    config = embedding_config(settings)

    assert (config.model, config.revision, config.dimension) == ("org/m", "abc", VECTOR_DIMENSION)
    assert (config.query_prefix, config.passage_prefix) == ("q: ", "p: ")
    assert config.max_seq_length == 256 and config.include_heading and config.normalized


def test_runtime_settings_do_not_change_the_fingerprint(make_settings: SettingsFactory) -> None:
    fast = make_settings(embedding_batch_size=64, embedding_device="cpu", embedding_torch_threads=2)
    slow = make_settings(embedding_batch_size=1, embedding_device="auto")

    assert embedding_config(fast).fingerprint() == embedding_config(slow).fingerprint()
    assert runtime_settings(fast) != runtime_settings(slow)
    assert runtime_settings(fast).batch_size == 64 and runtime_settings(fast).torch_threads == 2


# --- settings validation -------------------------------------------------------------------


def test_default_embedding_settings_pin_a_revision_and_match_the_database(
    make_settings: SettingsFactory,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="redis://r:6379/0",
        celery_broker_url="redis://r:6379/1",
        celery_result_backend="redis://r:6379/2",
        jwt_secret_key="x" * 40,
    )

    assert settings.embedding_model == "intfloat/multilingual-e5-small"
    assert len(settings.embedding_model_revision) == 40
    assert settings.embedding_dimension == VECTOR_DIMENSION == 384
    assert (
        settings.embedding_query_prefix == "query: "
        and settings.embedding_passage_prefix == "passage: "
    )
    assert settings.embedding_allow_download is True and settings.embedding_device == "auto"


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_provider": "sentence_transformers", "embedding_model_revision": "main"},
        {"embedding_provider": "sentence_transformers", "embedding_model_revision": "abc123"},
        {"embedding_dimension": 768},
        {"embedding_stale_after_seconds": 100},
        {"search_default_top_k": 60, "search_max_top_k": 50},
        {"embedding_batch_size": 0},
        {"search_max_query_chars": 5},
        {"embedding_device": "tpu"},
    ],
)
def test_unsafe_or_inconsistent_embedding_settings_are_rejected(
    make_settings: SettingsFactory, overrides: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        make_settings(**overrides)


def test_a_local_model_directory_does_not_need_a_commit_hash(
    make_settings: SettingsFactory, tmp_path: Any
) -> None:
    settings = make_settings(
        embedding_provider="sentence_transformers",
        embedding_model=str(tmp_path.resolve()),
        embedding_model_revision="local",
    )

    assert settings.embedding_model_revision == "local"


def test_time_limits_leave_room_for_loading_the_model(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        embedding_task_budget_seconds=100, embedding_model_load_allowance_seconds=60
    )

    assert settings.embedding_soft_time_limit_seconds == 190
    assert settings.embedding_hard_time_limit_seconds == 220


# --- vector validation ---------------------------------------------------------------------


def _unit(rows: int = 2, dim: int = VECTOR_DIMENSION) -> np.ndarray:
    matrix = np.random.default_rng(1).normal(size=(rows, dim)).astype(np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def test_valid_unit_vectors_pass_and_are_returned_as_float32() -> None:
    result = validate_vectors(_unit().astype(np.float64), make_config(), expected=2)

    assert result.dtype == np.float32 and result.shape == (2, VECTOR_DIMENSION)


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: _unit(3), "expected 2 vectors"),
        (lambda: _unit(dim=VECTOR_DIMENSION + 1), "dimension"),
        (lambda: np.full((2, VECTOR_DIMENSION), np.nan), "NaN"),
        (lambda: np.full((2, VECTOR_DIMENSION), np.inf), "NaN or infinite"),
        (lambda: np.zeros((2, VECTOR_DIMENSION)), "zero vector"),
        (lambda: _unit() * 3, "not unit length"),
        (lambda: np.ones(VECTOR_DIMENSION), "expected 2 vectors"),
    ],
    ids=["count", "dimension", "nan", "inf", "zeros", "not-normalised", "one-dimensional"],
)
def test_unusable_vectors_are_rejected_not_repaired(make: Any, message: str) -> None:
    with pytest.raises(InvalidEmbeddingError, match=message):
        validate_vectors(make(), make_config(), expected=2)


def test_unnormalised_vectors_are_fine_when_the_model_does_not_normalise() -> None:
    validate_vectors(_unit() * 3, make_config(normalized=False), expected=2)


# --- heading context -----------------------------------------------------------------------


def test_heading_context_is_only_added_when_enabled() -> None:
    assert passage_input(("Guide", "Setup"), "body", include_heading=True) == "Guide > Setup\nbody"
    assert passage_input(("Guide", "Setup"), "body", include_heading=False) == "body"
    assert passage_input((), "body", include_heading=True) == "body"


# --- test doubles honour the provider contract ------------------------------------------


def test_hashed_vectors_are_deterministic_unit_length_and_share_words() -> None:
    a = hashed_vector("alpha beta gamma")
    b = hashed_vector("Alpha  beta GAMMA")
    c = hashed_vector("delta epsilon zeta")

    assert np.allclose(a, b) and a.dtype == np.float32
    assert float(np.linalg.norm(a)) == pytest.approx(1.0, abs=1e-5)
    assert float(a @ hashed_vector("alpha beta delta")) > float(a @ c)


def test_providers_return_batches_of_the_right_shape_and_never_zero_vectors() -> None:
    provider = HashProvider(make_config())

    batch = provider.embed_documents(["one", "two words", "ইলিশ মাছ"])

    assert isinstance(batch, EmbeddingBatch) and batch.vectors.shape == (3, VECTOR_DIMENSION)
    assert (np.linalg.norm(batch.vectors, axis=1) > 0.99).all()
    assert provider.embed_documents([]).vectors.shape == (0, VECTOR_DIMENSION)
    assert provider.embed_query("one").shape == (VECTOR_DIMENSION,)


def test_controlled_provider_returns_exactly_the_dictated_vectors() -> None:
    provider = ControlledProvider(make_config(), {"a": [1.0, 0.0], "b": [0.0, 2.0]})

    vectors = provider.embed_documents(["a", "b"]).vectors

    assert vectors[0][:2].tolist() == [1.0, 0.0] and vectors[1][:2].tolist() == [0.0, 1.0]


# --- registry ---------------------------------------------------------------------------------


def test_registry_creates_one_provider_per_configuration(make_settings: SettingsFactory) -> None:
    settings = make_settings()

    first = get_provider(settings)
    second = get_provider(settings)
    changed = get_provider(make_settings(embedding_model_revision="other"))

    assert first is second and changed is not first
    assert isinstance(first, HashProvider)


def test_registry_rejects_unknown_providers_clearly(make_settings: SettingsFactory) -> None:
    with pytest.raises(ModelUnavailableError, match="Unknown EMBEDDING_PROVIDER 'nope'"):
        get_provider(make_settings(embedding_provider="nope"))


def test_new_providers_can_be_registered_without_touching_the_pipeline(
    make_settings: SettingsFactory,
) -> None:
    register_provider("custom", lambda config, runtime: HashProvider(config))
    try:
        assert (
            "custom" in available_providers() and "sentence_transformers" in available_providers()
        )
        assert isinstance(get_provider(make_settings(embedding_provider="custom")), HashProvider)
    finally:
        registry.unregister_provider("custom")
    assert "custom" not in available_providers()


def test_resetting_the_cache_forgets_loaded_providers(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    first = get_provider(settings)

    reset_provider_cache()

    assert get_provider(settings) is not first


# --- the sentence-transformers provider (without the real model) ---------------------------


class _Tokenizer:
    def __init__(self, lengths: dict[str, int]) -> None:
        self.lengths = lengths

    def __call__(self, texts: list[str], **kwargs: Any) -> dict[str, list[list[int]]]:
        assert kwargs == {"add_special_tokens": True, "truncation": False}
        return {"input_ids": [[0] * self.lengths.get(t, 5) for t in texts]}


class _FakeModel:
    max_seq_length = 512

    def __init__(self, dimension: int = VECTOR_DIMENSION, native: int = 512) -> None:
        self._dimension = dimension
        self.max_seq_length = native
        self.encoded: list[list[str]] = []
        self.tokenizer = _Tokenizer({"passage: very long": 900})

    def get_sentence_embedding_dimension(self) -> int:  # the pre-6 name
        return self._dimension

    def encode(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        self.encoded.append(list(texts))
        assert kwargs["normalize_embeddings"] is True and kwargs["show_progress_bar"] is False
        return np.vstack([hashed_vector(t, self._dimension) for t in texts])


def install_fake_library(monkeypatch: pytest.MonkeyPatch, **behaviour: Any) -> dict[str, Any]:
    """Provide fake `sentence_transformers` and `torch` modules and record how the model loads."""
    calls: dict[str, Any] = {}

    class SentenceTransformer(_FakeModel):
        def __init__(self, name: str, **kwargs: Any) -> None:
            if error := behaviour.get("error"):
                raise error
            super().__init__(
                behaviour.get("dimension", VECTOR_DIMENSION), behaviour.get("native", 512)
            )
            calls["name"], calls["kwargs"] = name, kwargs

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = SentenceTransformer  # type: ignore[attr-defined]
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: behaviour.get("cuda", False))  # type: ignore[attr-defined]
    fake_torch.backends = types.SimpleNamespace()  # type: ignore[attr-defined]
    fake_torch.set_num_threads = lambda n: calls.__setitem__("threads", n)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    return calls


def st_config(**overrides: Any) -> EmbeddingConfig:
    values: dict[str, Any] = {
        "provider": "sentence_transformers",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    }
    values.update(overrides)
    return make_config(**values)


def test_the_model_is_loaded_pinned_safetensors_only_and_without_remote_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(
        st_config(), RuntimeSettings(device="cpu", cache_dir="/models", allow_download=True)
    )

    provider.warmup()

    kwargs = calls["kwargs"]
    assert calls["name"] == "org/model" and kwargs["revision"] == REVISION
    assert kwargs["trust_remote_code"] is False
    assert kwargs["model_kwargs"] == {"use_safetensors": True}
    assert kwargs["cache_folder"] == "/models" and kwargs["local_files_only"] is False
    assert kwargs["device"] == "cpu" and provider.device == "cpu"


def test_downloads_can_be_disabled_for_offline_use(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_library(monkeypatch)

    SentenceTransformerProvider(st_config(), RuntimeSettings(allow_download=False)).warmup()

    assert calls["kwargs"]["local_files_only"] is True


def test_the_model_is_loaded_once_even_when_used_from_many_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    created: list[int] = []
    install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(st_config(), RuntimeSettings())
    original = provider._load_model

    def counting() -> Any:
        created.append(1)
        return original()

    provider._load_model = counting  # type: ignore[method-assign]
    threads = [threading.Thread(target=provider.warmup) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert created == [1]


def test_prefixes_are_applied_and_truncation_is_reported_per_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(st_config(), RuntimeSettings(batch_size=8))

    batch = provider.embed_documents(["very long", "short"])
    query = provider.embed_query("what")

    assert batch.truncated == (True, False) and batch.truncated_count == 1
    assert provider._model.encoded == [["passage: very long", "passage: short"], ["query: what"]]
    assert batch.vectors.shape == (2, VECTOR_DIMENSION) and query.shape == (VECTOR_DIMENSION,)


@pytest.mark.parametrize(
    ("behaviour", "match"),
    [
        ({"dimension": 768}, "768-dimensional"),
        ({"native": 128}, "exceeds the model limit"),
    ],
)
def test_a_mismatched_model_fails_clearly_at_load_time(
    monkeypatch: pytest.MonkeyPatch, behaviour: dict[str, Any], match: str
) -> None:
    install_fake_library(monkeypatch, **behaviour)

    with pytest.raises(ModelUnavailableError, match=match):
        SentenceTransformerProvider(st_config(), RuntimeSettings()).warmup()


@pytest.mark.parametrize(
    ("error", "allow_download", "retryable"),
    [
        (OSError("network down at /secret/path"), True, True),
        (OSError("not cached at /secret/path"), False, False),
        (ValueError("bad config"), True, False),
    ],
)
def test_load_failures_are_classified_and_never_leak_paths(
    monkeypatch: pytest.MonkeyPatch, error: Exception, allow_download: bool, retryable: bool
) -> None:
    install_fake_library(monkeypatch, error=error)
    provider = SentenceTransformerProvider(
        st_config(), RuntimeSettings(allow_download=allow_download)
    )

    with pytest.raises(ModelUnavailableError) as raised:
        provider.warmup()

    assert raised.value.retryable is retryable
    assert "/secret" not in str(raised.value) and "org/model" in str(raised.value)


def test_missing_ml_libraries_are_reported_as_an_unavailable_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setitem(sys.modules, "torch", None)

    with pytest.raises(ModelUnavailableError, match="required for local embeddings"):
        SentenceTransformerProvider(st_config(), RuntimeSettings()).warmup()


def test_inference_errors_become_retryable_embedding_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(st_config(), RuntimeSettings())
    provider.warmup()

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("CUDA out of memory at /secret")

    provider._model.encode = explode
    with pytest.raises(EmbeddingFailedError) as raised:
        provider.embed_documents(["x"])

    assert raised.value.retryable and "/secret" not in str(raised.value)


def test_a_model_that_returns_garbage_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(st_config(), RuntimeSettings())
    provider.warmup()
    provider._model.encode = lambda texts, **kw: np.full((len(texts), VECTOR_DIMENSION), np.nan)

    with pytest.raises(InvalidEmbeddingError):
        provider.embed_query("x")


def test_device_resolution_prefers_cuda_then_cpu_and_refuses_missing_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.embeddings.sentence_transformer import resolve_device

    install_fake_library(monkeypatch, cuda=True)
    assert resolve_device("auto") == "cuda" and resolve_device("cpu") == "cpu"
    install_fake_library(monkeypatch, cuda=False)
    assert resolve_device("auto") == "cpu"
    with pytest.raises(ModelUnavailableError, match="CUDA is not available"):
        resolve_device("cuda")


def test_thread_count_is_applied_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_library(monkeypatch)

    SentenceTransformerProvider(st_config(), RuntimeSettings(torch_threads=3)).warmup()

    assert calls["threads"] == 3


def test_the_renamed_dimension_method_of_newer_libraries_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Newer(_FakeModel):
        get_sentence_embedding_dimension = None  # type: ignore[assignment]

        def get_embedding_dimension(self) -> int:
            return VECTOR_DIMENSION

    install_fake_library(monkeypatch)
    sys.modules["sentence_transformers"].SentenceTransformer = lambda name, **kw: Newer()  # type: ignore[attr-defined]

    provider = SentenceTransformerProvider(st_config(), RuntimeSettings())
    provider.warmup()

    assert provider.embed_query("x").shape == (VECTOR_DIMENSION,)


def test_a_tokenizer_failure_is_reported_as_a_retryable_embedding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_library(monkeypatch)
    provider = SentenceTransformerProvider(st_config(), RuntimeSettings())
    provider.warmup()

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tokenizer exploded on /secret")

    provider._model.tokenizer = broken
    with pytest.raises(EmbeddingFailedError) as raised:
        provider.embed_documents(["x"])

    assert raised.value.retryable and "/secret" not in str(raised.value)


def test_the_download_command_reports_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.embeddings import download

    provider = HashProvider(make_config())
    seen: dict[str, Any] = {}

    def fake_get_provider(settings: Any) -> HashProvider:
        seen["allow_download"] = settings.embedding_allow_download
        return provider

    monkeypatch.setattr(download, "get_provider", fake_get_provider)
    monkeypatch.setattr(download, "get_settings", lambda: _settings())

    assert download.main() == 0
    assert seen["allow_download"] is True  # the command always may download
    assert "ready: loaded on fake" in capsys.readouterr().out

    def failing(settings: Any) -> HashProvider:
        raise ModelUnavailableError("no network")

    monkeypatch.setattr(download, "get_provider", failing)
    assert download.main() == 1
    assert "FAILED: no network" in capsys.readouterr().err


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="redis://r",
        celery_broker_url="redis://r",
        celery_result_backend="redis://r",
        jwt_secret_key="x" * 40,
        embedding_allow_download=False,
    )


@pytest.mark.asyncio
async def test_the_dispatcher_publishes_embedding_tasks_and_survives_a_dead_broker() -> None:
    from app.workers.dispatch import EMBED_DOCUMENT_TASK, CeleryProcessingQueue

    sent: list[tuple[str, list[str]]] = []

    class Celery:
        def send_task(self, name: str, args: list[str], ignore_result: bool) -> None:
            sent.append((name, args))

    class Dead:
        def send_task(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("broker down")

    document = uuid.uuid4()
    assert await CeleryProcessingQueue(Celery()).enqueue_embedding(document) is True
    assert sent == [(EMBED_DOCUMENT_TASK, [str(document)])]
    assert await CeleryProcessingQueue(Dead()).enqueue_embedding(document) is False
