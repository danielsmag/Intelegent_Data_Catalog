"""JSON schema output for models that support it.

A tool call lets Qwen think until the answer is empty.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from intelegent_data_catalog.llm.types import StructuredMethod


def structured_method(model: BaseChatModel) -> StructuredMethod:
    """Prefer JSON schema. Claude answers through a tool call instead."""
    module: str = type(model).__module__
    if "anthropic" in module:
        return "function_calling"
    return "json_schema"
