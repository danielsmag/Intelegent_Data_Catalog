"""LangGraph agent used by a table agent to run one catalog operation."""

from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.llm.factory import build_table_operation_agent

__all__ = ["TableOperationAgent", "build_table_operation_agent"]
