"""Build the Pydantic AI model selected in settings."""

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.core.settings import LlmSettings, ReasoningEffort
from intelegent_data_catalog.exceptions import LlmUnavailableError
from intelegent_data_catalog.llm.airflow_analysis import AirflowAnalysisAgent
from intelegent_data_catalog.llm.airflow_context import AirflowContextAgent
from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.llm.column_graph import ColumnSubgraphAgent
from intelegent_data_catalog.llm.dag_context import DagContextAgent
from intelegent_data_catalog.llm.file_context import FileContextAgent


def thinking_level(effort: ReasoningEffort) -> ThinkingLevel:
    """Map a saved effort onto the model setting. ``off`` disables thinking."""
    if effort == "off":
        return False
    return effort


def build_table_operation_agent(
    settings: LlmSettings,
    thinking: ThinkingLevel = False,
) -> TableOperationAgent:
    """Return an agent for ``settings.provider``. Ollama is the local default.

    Context writes share this agent, so the timeout matches the other structured calls.
    """
    return TableOperationAgent(
        build_model(settings),
        max_requests=settings.max_tool_steps,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout_seconds=max(settings.timeout_seconds, 600.0),
        thinking=thinking,
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


class CatalogModels:
    """One profile's agents. Each agent gets its own model client."""

    def __init__(
        self,
        settings: LlmSettings,
        *,
        text_chars: int,
        column_graph: bool,
        thinking: ThinkingLevel = False,
    ) -> None:
        timeout: float = max(settings.timeout_seconds, 600.0)
        self.settings: LlmSettings = settings
        self.thinking: ThinkingLevel = thinking
        self.operation: TableOperationAgent = build_table_operation_agent(settings, thinking)
        self.column: ColumnSubgraphAgent | None = None
        if column_graph:
            self.column = ColumnSubgraphAgent(
                build_model(settings),
                max_requests=settings.max_tool_steps,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout_seconds=timeout,
                thinking=thinking,
            )
        self.airflow_proposer: AirflowAnalysisAgent = AirflowAnalysisAgent(
            build_model(settings),
            max_requests=settings.max_tool_steps,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout_seconds=timeout,
            thinking=thinking,
        )
        self.airflow_describer: AirflowContextAgent = AirflowContextAgent(
            build_model(settings),
            max_requests=settings.max_tool_steps,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout_seconds=timeout,
            thinking=thinking,
        )
        self.dag_describer: DagContextAgent = DagContextAgent(
            build_model(settings),
            max_requests=settings.max_tool_steps,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout_seconds=timeout,
            thinking=thinking,
        )
        self.file_describer: FileContextAgent = FileContextAgent(
            build_model(settings),
            max_requests=settings.max_tool_steps,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout_seconds=timeout,
            thinking=thinking,
        )
