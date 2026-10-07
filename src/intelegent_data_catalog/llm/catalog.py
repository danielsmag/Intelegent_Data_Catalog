"""Short-lived Pydantic AI agent for one catalog operation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from intelegent_data_catalog.exceptions import LlmError, LlmUnavailableError

if TYPE_CHECKING:
    from intelegent_data_catalog.llm.tools.table import TableToolSource


class TableOperationAgent:
    """Runs one catalog operation. Tools read the catalog and can publish Neo4j."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._max_requests: int = max_requests
        self._settings: ModelSettings = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout_seconds,
        }
        self._agent: Agent[TableToolSource, str] = _table_agent(model)

    def run(self, instruction: str, source: TableToolSource, instructions: str) -> str:
        """Run the model until the operation finishes or the request limit is reached."""
        return asyncio.run(self._run(instruction, source, instructions))

    async def _run(self, instruction: str, source: TableToolSource, instructions: str) -> str:
        try:
            result: AgentRunResult[str] = await self._agent.run(
                instruction,
                deps=source,
                instructions=instructions,
                model_settings=self._settings,
                usage_limits=UsageLimits(request_limit=self._max_requests),
            )
        except UsageLimitExceeded as err:
            raise LlmError("language model exceeded the tool step limit") from err
        except ModelHTTPError as err:
            raise LlmError(f"language model returned {err.status_code}") from err
        except ModelAPIError as err:
            raise LlmError("language model is unavailable") from err
        except UnexpectedModelBehavior as err:
            raise LlmError("language model returned an unexpected response") from err
        except ToolRetryError as err:
            raise LlmError("language model returned an unexpected response") from err
        except UserError as err:
            raise LlmUnavailableError("language model is not configured") from err
        answer: str = result.output
        return answer


def _table_agent(model: Model) -> Agent[TableToolSource, str]:
    """Register catalog tools after this module has finished importing."""
    from intelegent_data_catalog.llm.tools.table import (
        TableToolSource,
        compare_columns,
        get_column_profile,
        get_data_profile,
        get_lineage,
        get_metadata,
        get_neighbors,
        publish_graph,
    )

    agent: Agent[TableToolSource, str] = Agent(
        model,
        deps_type=TableToolSource,
        output_type=str,
        name="table-catalog",
    )
    agent.tool(get_metadata)
    agent.tool(get_data_profile)
    agent.tool(get_column_profile)
    agent.tool(compare_columns)
    agent.tool(get_neighbors)
    agent.tool(get_lineage)
    agent.tool(publish_graph)
    return agent
