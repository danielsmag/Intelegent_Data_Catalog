"""Shared model-call types. Agent modules and the factory both use these."""

from typing import Literal

from intelegent_data_catalog.core.settings import ReasoningEffort

ThinkingLevel = Literal[False] | ReasoningEffort
StructuredMethod = Literal["json_schema", "function_calling"]
