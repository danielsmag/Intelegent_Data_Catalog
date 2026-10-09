"""Build the Pydantic AI model selected in settings."""

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from intelegent_data_catalog.core.settings import LlmSettings
from intelegent_data_catalog.exceptions import LlmUnavailableError
from intelegent_data_catalog.llm.catalog import TableOperationAgent


def build_table_operation_agent(settings: LlmSettings) -> TableOperationAgent:
    """Return an agent for ``settings.provider``. Ollama is the local default.

    Context writes share this agent, so the timeout matches the other structured calls.
    """
    return TableOperationAgent(
        build_model(settings),
        max_requests=settings.max_tool_steps,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout_seconds=max(settings.timeout_seconds, 600.0),
    )


def build_model(settings: LlmSettings) -> Model:
    """Return the provider model. Remote providers require ``CATALOG_LLM__API_KEY``."""
    match settings.provider:
        case "ollama":
            return OllamaModel(
                settings.model,
                provider=OllamaProvider(
                    base_url=_chat_base_url(settings.base_url),
                    api_key=_optional_key(settings),
                ),
            )
        case "openai":
            return OpenAIChatModel(
                settings.model,
                provider=OpenAIProvider(
                    base_url=_chat_base_url(settings.base_url),
                    api_key=_require_key(settings, "openai"),
                ),
            )
        case "claude":
            return AnthropicModel(
                settings.model,
                provider=AnthropicProvider(
                    api_key=_require_key(settings, "claude"),
                    base_url=settings.base_url,
                ),
            )


def _chat_base_url(base_url: str) -> str:
    """Ollama and OpenAI speak the chat API under ``/v1``."""
    trimmed: str = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def _optional_key(settings: LlmSettings) -> str | None:
    key: str = settings.api_key.get_secret_value().strip()
    if key == "":
        return None
    return key


def _require_key(settings: LlmSettings, provider: str) -> str:
    key: str | None = _optional_key(settings)
    if key is None:
        raise LlmUnavailableError(f"{provider} requires CATALOG_LLM__API_KEY")
    return key
