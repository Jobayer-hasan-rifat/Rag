from types import SimpleNamespace
from typing import Any

import pytest

from app.reranking.cross_encoder import CrossEncoderReranker, RerankRuntime
from app.reranking.registry import get_reranker
from app.reranking.types import RerankerConfig, RerankerUnavailableError
from tests.conftest import SettingsFactory


def model_config(**values: Any) -> Any:
    defaults: dict[str, Any] = {
        "architectures": ["XLMRobertaForSequenceClassification"],
        "num_labels": 1,
        "auto_map": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_a_plain_single_output_sequence_classifier_is_accepted() -> None:
    CrossEncoderReranker._validate_architecture(model_config())
    CrossEncoderReranker._validate_architecture(
        model_config(architectures=["BertForSequenceClassification"])
    )


def test_a_model_with_custom_code_is_refused() -> None:
    config = model_config(auto_map={"AutoModel": "modeling.Custom"})

    with pytest.raises(RerankerUnavailableError, match="custom code"):
        CrossEncoderReranker._validate_architecture(config)


@pytest.mark.parametrize(
    "architectures", [None, [], ["NewForSequenceClassification"], ["XLMRobertaModel"]]
)
def test_unknown_architectures_are_refused(architectures: list[str] | None) -> None:
    with pytest.raises(RerankerUnavailableError, match="architecture"):
        CrossEncoderReranker._validate_architecture(model_config(architectures=architectures))


@pytest.mark.parametrize("labels", [0, 2, 3])
def test_a_model_without_exactly_one_output_is_refused(labels: int) -> None:
    with pytest.raises(RerankerUnavailableError, match="one output"):
        CrossEncoderReranker._validate_architecture(model_config(num_labels=labels))


def test_a_missing_model_with_downloads_disabled_fails_safely_without_the_network(
    tmp_path: Any,
) -> None:
    config = RerankerConfig("cross_encoder", "no-such-org/no-such-reranker", "0" * 40, 512)
    reranker = CrossEncoderReranker(
        config, RerankRuntime(device="cpu", cache_dir=str(tmp_path), allow_download=False)
    )

    with pytest.raises(RerankerUnavailableError) as error:
        reranker.warmup()

    assert "downloads are disabled" in str(error.value)
    assert str(tmp_path) not in str(error.value)  # library messages (paths) are never forwarded
    assert reranker.device == "unloaded"


def test_an_unknown_provider_is_reported(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_provider="nope")

    with pytest.raises(RerankerUnavailableError, match="Unknown RERANK_PROVIDER"):
        get_reranker(settings)


def test_getting_a_reranker_does_not_load_a_model(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_enabled=True)

    reranker = get_reranker(settings)

    assert reranker.device == "unloaded" and get_reranker(settings) is reranker  # one per process


def test_an_enabled_cross_encoder_needs_a_pinned_commit(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="RERANK_MODEL_REVISION"):
        make_settings(
            rerank_enabled=True, rerank_provider="cross_encoder", rerank_model_revision="main"
        )
    make_settings(
        rerank_enabled=False, rerank_provider="cross_encoder", rerank_model_revision="main"
    )


def test_the_default_candidate_count_may_not_exceed_the_maximum(
    make_settings: SettingsFactory,
) -> None:
    with pytest.raises(ValueError, match="RERANK_CANDIDATES"):
        make_settings(rerank_candidates=60, rerank_max_candidates=50)


def test_reranking_is_off_by_default(make_settings: SettingsFactory) -> None:
    assert make_settings().rerank_enabled is False


def test_a_load_that_can_never_work_is_remembered_and_not_retried(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from transformers import AutoConfig

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "architectures": ["BertForSequenceClassification"],
                "model_type": "bert",
                "num_labels": 1,
                "max_position_embeddings": 100,
            }
        )
    )
    reads = []
    original = AutoConfig.from_pretrained

    def counting(*args: Any, **kwargs: Any) -> Any:
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(AutoConfig, "from_pretrained", counting)
    reranker = CrossEncoderReranker(
        RerankerConfig("cross_encoder", str(tmp_path), "0" * 40, 512), RerankRuntime(device="cpu")
    )

    for _ in range(3):
        with pytest.raises(RerankerUnavailableError, match="RERANK_MAX_LENGTH"):
            reranker.warmup()

    assert len(reads) == 1  # checked once, before any weights, then remembered
