"""Pydantic AI model selection for the table question agent."""

import pytest
from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel

from intelegent_data_catalog.core.settings import LlmProvider, LlmSettings, load_settings
from intelegent_data_catalog.exceptions import LlmUnavailableError, SettingsError
from intelegent_data_catalog.llm.factory import build_model


def test_provider_defaults() -> None:
    """Empty model and base URL pick the provider default."""
    claude: LlmSettings = LlmSettings(provider="claude")
    openai: LlmSettings = LlmSettings(provider="openai")
    local: LlmSettings = LlmSettings(
        provider="ollama",
        model="qwen2.5",
        base_url="http://127.0.0.1:11434/",
    )
    assert claude.model == "claude-sonnet-4-5"
    assert claude.base_url == "https://api.anthropic.com"
    assert openai.model == "gpt-4.1"
    assert openai.base_url == "https://api.openai.com"
    assert local.model == "qwen2.5"
    assert local.base_url == "http://127.0.0.1:11434"


def test_load_settings_defaults_to_local_ollama() -> None:
    """settings.toml leaves the model local until an operator changes it."""
    loaded: LlmSettings = load_settings().llm
    assert loaded.provider == "ollama"
    assert loaded.model == "llama3.2"
    assert loaded.base_url == "http://127.0.0.1:11434"
    assert loaded.api_key.get_secret_value() == ""


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider outside claude, openai, and ollama fails settings load."""
    monkeypatch.setenv("CATALOG_LLM__PROVIDER", "gemini")
    with pytest.raises(SettingsError):
        load_settings()


def test_build_model_uses_local_ollama() -> None:
    """The default provider is the Ollama server on this computer."""
    model: object = build_model(LlmSettings(provider="ollama"))
    assert isinstance(model, OllamaModel)


@pytest.mark.parametrize(
    ("provider", "kind"),
    [
        ("openai", OpenAIChatModel),
        ("claude", AnthropicModel),
    ],
)
def test_build_model_uses_remote_provider(provider: LlmProvider, kind: type[object]) -> None:
    """Claude and OpenAI are built only when an API key is present."""
    model: object = build_model(LlmSettings(provider=provider, api_key=SecretStr("test-key")))
    assert isinstance(model, kind)


@pytest.mark.parametrize("provider", ["claude", "openai"])
def test_remote_provider_requires_api_key(provider: LlmProvider) -> None:
    """Claude and OpenAI do not build a client when the key is missing."""
    settings: LlmSettings = LlmSettings(provider=provider, api_key=SecretStr("  "))
    with pytest.raises(LlmUnavailableError, match="CATALOG_LLM__API_KEY"):
        build_model(settings)
