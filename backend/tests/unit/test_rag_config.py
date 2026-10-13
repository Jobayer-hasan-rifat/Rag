import pytest

from tests.conftest import SettingsFactory


def test_answer_generation_is_off_by_default(make_settings: SettingsFactory) -> None:
    settings = make_settings()

    assert settings.rag_enabled is False and settings.rag_retrieval_mode == "hybrid"
    assert settings.llm_temperature <= 0.2  # conservative for grounded answers


def test_valid_settings_are_accepted_when_enabled(make_settings: SettingsFactory) -> None:
    make_settings(rag_enabled=True)
    make_settings(
        rag_enabled=True,
        llm_provider="openai_compatible",
        llm_base_url="https://api.example.com/v1",
        llm_api_key="sk-x",
    )


@pytest.mark.parametrize("url", ["ftp://host", "localhost:11434", "http://", ""])
def test_the_base_url_must_be_http_or_https(make_settings: SettingsFactory, url: str) -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        make_settings(rag_enabled=True, llm_base_url=url)


def test_a_key_is_never_sent_over_plain_http_to_a_remote_host(
    make_settings: SettingsFactory,
) -> None:
    with pytest.raises(ValueError, match="plain http"):
        make_settings(rag_enabled=True, llm_base_url="http://api.example.com", llm_api_key="k")
    make_settings(rag_enabled=True, llm_base_url="http://localhost:11434", llm_api_key="k")


def test_a_remote_openai_compatible_endpoint_needs_a_key(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY is required"):
        make_settings(
            rag_enabled=True,
            llm_provider="openai_compatible",
            llm_base_url="https://api.example.com/v1",
        )
    make_settings(
        rag_enabled=True, llm_provider="openai_compatible", llm_base_url="http://localhost:1234/v1"
    )


def test_the_budgets_must_fit_in_the_model_window(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="must fit in LLM_CONTEXT_TOKENS"):
        make_settings(
            rag_enabled=True,
            llm_context_tokens=2048,
            rag_context_token_budget=2400,
            llm_max_output_tokens=400,
        )


def test_sources_cannot_exceed_candidates(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="RAG_MAX_SOURCES"):
        make_settings(rag_enabled=True, rag_candidates=3, rag_max_sources=5)


def test_an_empty_key_means_unset(make_settings: SettingsFactory) -> None:
    assert make_settings(llm_api_key="").llm_api_key is None


def test_credentials_inside_the_url_are_refused(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="must not contain credentials"):
        make_settings(rag_enabled=True, llm_base_url="https://user:pw@api.example.com/v1")
