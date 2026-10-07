"""Pydantic AI agent used by a table agent to answer one question."""

from intelegent_data_catalog.llm.catalog import TableQuestionAgent
from intelegent_data_catalog.llm.factory import build_table_question_agent

__all__ = ["TableQuestionAgent", "build_table_question_agent"]
