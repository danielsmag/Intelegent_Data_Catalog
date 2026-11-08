"""Catalog agents: the object spaces, scopes, and workspaces hold."""

from intelegent_data_catalog.agents.models import (
    AgentAnalysisContext,
    AgentContextOptions,
    AgentListItem,
    AgentPage,
    AgentRead,
)

__all__ = [
    "AgentAnalysisContext",
    "AgentContextOptions",
    "AgentListItem",
    "AgentPage",
    "AgentRead",
    "AgentService",
]


def __getattr__(name: str) -> object:
    """Load ``AgentService`` only when asked. Importing this package stays light."""
    if name == "AgentService":
        from intelegent_data_catalog.agents.service import AgentService

        return AgentService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
