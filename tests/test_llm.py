"""Pydantic AI model selection for the table question agent."""

import asyncio
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.settings import (
    AppSettings,
    LlmProvider,
    LlmSettings,
    MarquezSettings,
    ModelEndpoint,
    ModelId,
    Neo4jSettings,
    ReasoningEffort,
    TrinoSettings,
    load_settings,
)
from intelegent_data_catalog.exceptions import (
    CatalogError,
    LlmError,
    LlmUnavailableError,
    SettingsError,
)
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.llm.factory import CatalogModels, build_model
from intelegent_data_catalog.llm.run import ModelCall


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


def test_load_settings_defaults_to_gpt() -> None:
    """settings.toml starts on GPT-4.1 with thinking effort low."""
    loaded: LlmSettings = load_settings().llm
    assert loaded.provider == "openai"
    assert loaded.model == "gpt-4.1"
    assert loaded.base_url == "https://api.openai.com"
    assert loaded.effort == "low"
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


def test_local_profile_uses_the_ollama_block() -> None:
    """The local key keeps the Ollama endpoint and its timeout."""
    parent: LlmSettings = LlmSettings(provider="ollama", model="qwen3:4b", timeout_seconds=90)
    chosen: LlmSettings = parent.profile("local")
    assert chosen.provider == "ollama"
    assert chosen.model == "qwen3:4b"
    assert chosen.timeout_seconds == 90
    assert isinstance(build_model(chosen), OllamaModel)


def test_gpt_profile_uses_the_openai_block() -> None:
    """The gpt key keeps an OpenAI block, including its key."""
    parent: LlmSettings = LlmSettings(
        provider="openai",
        api_key=SecretStr("parent-key"),
        model="gpt-4.1-mini",
        temperature=0.2,
    )
    chosen: LlmSettings = parent.profile("gpt")
    assert chosen.provider == "openai"
    assert chosen.model == "gpt-4.1-mini"
    assert chosen.api_key.get_secret_value() == "parent-key"
    assert chosen.temperature == 0.2
    assert isinstance(build_model(chosen), OpenAIChatModel)


def test_gpt_section_overrides_a_local_default() -> None:
    """An explicit gpt section wins while local still uses Ollama."""
    parent: LlmSettings = LlmSettings(
        provider="ollama",
        model="qwen3:4b",
        gpt=ModelEndpoint(api_key=SecretStr("gpt-key"), model="gpt-4.1-mini"),
    )
    chosen: LlmSettings = parent.profile("gpt")
    assert chosen.provider == "openai"
    assert chosen.model == "gpt-4.1-mini"
    assert chosen.api_key.get_secret_value() == "gpt-key"
    local: LlmSettings = parent.profile("local")
    assert local.provider == "ollama"
    assert local.model == "qwen3:4b"


def test_unset_gpt_profile_requires_a_key() -> None:
    """GPT with no key fails when the model is built."""
    chosen: LlmSettings = LlmSettings(provider="ollama").profile("gpt")
    assert chosen.provider == "openai"
    assert chosen.model == "gpt-4.1"
    with pytest.raises(LlmUnavailableError, match="CATALOG_LLM__API_KEY"):
        build_model(chosen)


def test_gpt_api_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """CATALOG_LLM__GPT__API_KEY is the key used for a GPT catalog model."""
    monkeypatch.setenv("CATALOG_LLM__GPT__API_KEY", "from-env")
    loaded: LlmSettings = load_settings().llm
    chosen: LlmSettings = loaded.profile("gpt")
    assert loaded.provider == "openai"
    assert chosen.provider == "openai"
    assert chosen.api_key.get_secret_value() == "from-env"
    assert isinstance(build_model(chosen), OpenAIChatModel)


def test_gpt_4_1_completion_budget_is_capped() -> None:
    """GPT-4.1 mini rejects a completion budget above 32768 with HTTP 400."""
    settings: LlmSettings = LlmSettings(
        provider="openai",
        model="gpt-4.1-mini",
        api_key=SecretStr("test-key"),
        max_tokens=100_000,
    )
    models: CatalogModels = CatalogModels(settings, text_chars=20, column_graph=False)
    assert models.space_describer._call._settings["max_tokens"] == 32_768
    plain: LlmSettings = LlmSettings(
        provider="openai",
        model="gpt-4.1",
        api_key=SecretStr("test-key"),
        max_tokens=100_000,
    )
    plain_models: CatalogModels = CatalogModels(plain, text_chars=20, column_graph=False)
    assert plain_models.space_describer._call._settings["max_tokens"] == 32_768


def test_smaller_completion_budget_is_kept() -> None:
    """A budget under the model cap is sent as configured."""
    settings: LlmSettings = LlmSettings(
        provider="openai",
        model="gpt-4.1-mini",
        api_key=SecretStr("test-key"),
        max_tokens=2048,
    )
    models: CatalogModels = CatalogModels(settings, text_chars=20, column_graph=False)
    assert models.space_describer._call._settings["max_tokens"] == 2048


def test_gpt_6_astra_keeps_the_configured_completion_budget() -> None:
    """GPT-6 Astra accepts the 100000 completion budget from settings."""
    settings: LlmSettings = LlmSettings(
        provider="openai",
        model="gpt-6-astra",
        api_key=SecretStr("test-key"),
        max_tokens=100_000,
    )
    models: CatalogModels = CatalogModels(settings, text_chars=20, column_graph=False)
    assert models.space_describer._call._settings["max_tokens"] == 100_000


def test_claude_completion_budget_follows_the_model_profile() -> None:
    """Claude rejects a completion budget above the output cap on its profile."""
    settings: LlmSettings = LlmSettings(
        provider="claude",
        api_key=SecretStr("test-key"),
        max_tokens=100_000,
    )
    models: CatalogModels = CatalogModels(settings, text_chars=20, column_graph=False)
    cap: object = build_model(settings).profile.get("anthropic_max_output_tokens")
    assert isinstance(cap, int)
    assert models.space_describer._call._settings["max_tokens"] == cap


def test_specific_model_ids_keep_their_names() -> None:
    """A catalog id selects that model name and the matching provider."""
    parent: LlmSettings = LlmSettings(
        provider="ollama",
        model="llama3.2",
        gpt=ModelEndpoint(api_key=SecretStr("gpt-key")),
    )
    qwen: LlmSettings = parent.profile("qwen3:4b")
    assert qwen.provider == "ollama"
    assert qwen.model == "qwen3:4b"
    assert isinstance(build_model(qwen), OllamaModel)
    mini: LlmSettings = parent.profile("gpt-4.1-mini")
    assert mini.provider == "openai"
    assert mini.model == "gpt-4.1-mini"
    assert mini.api_key.get_secret_value() == "gpt-key"
    higher: LlmSettings = parent.profile("gpt-6-astra")
    assert higher.provider == "openai"
    assert higher.model == "gpt-6-astra"
    assert higher.api_key.get_secret_value() == "gpt-key"
    assert isinstance(build_model(higher), OpenAIChatModel)


def test_knowledge_build_constructs_the_gpt_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A table build with GPT-4.1 mini constructs agents from that OpenAI model."""
    settings: AppSettings = AppSettings(
        llm=LlmSettings(gpt=ModelEndpoint(api_key=SecretStr("test-key"))),
        neo4j=Neo4jSettings(enabled=False),
        marquez=MarquezSettings(enabled=False),
        trino=TrinoSettings(enabled=False),
    )
    session: Session = MagicMock(spec=Session)
    query: QueryService = MagicMock(spec=QueryService)
    platform: RequestPlatform = RequestPlatform(session, settings, query)
    seen: list[CatalogModels] = []
    saved: Callable[[RequestPlatform, ModelId, ReasoningEffort], CatalogModels] = (
        RequestPlatform._models
    )

    def capture(self: RequestPlatform, key: ModelId, effort: ReasoningEffort) -> CatalogModels:
        built: CatalogModels = saved(self, key, effort)
        seen.append(built)
        raise CatalogError("stop")

    monkeypatch.setattr(RequestPlatform, "_models", capture)
    with pytest.raises(CatalogError, match="stop"):
        platform.build_knowledge_context(
            uuid4(),
            TableContextOptions(model="gpt-4.1-mini", effort="high"),
        )
    assert len(seen) == 1
    assert seen[0].settings.provider == "openai"
    assert seen[0].settings.model == "gpt-4.1-mini"
    assert seen[0].thinking == "high"
    assert seen[0].settings.api_key.get_secret_value() == "test-key"


def test_model_call_stops_when_the_total_timeout_elapses() -> None:
    """A stream that keeps sending bytes still stops at the total timeout."""
    agent: _HangingAgent = _HangingAgent()
    call: ModelCall[None, str] = ModelCall(
        cast(Agent[None, str], agent),
        name="scope-context",
        max_requests=1,
        temperature=0.0,
        max_tokens=16,
        timeout_seconds=0.05,
    )
    with pytest.raises(LlmError, match="timed out"):
        call.run("summarize", None)


class _HangingAgent:
    """Model stand-in that never returns. The total timeout must cut it off."""

    def __init__(self) -> None:
        self.model: object = object()

    async def run(self, *args: object, **kwargs: object) -> str:
        """Sleep until the caller cancels the call."""
        del args, kwargs
        await asyncio.sleep(30)
        return ""
