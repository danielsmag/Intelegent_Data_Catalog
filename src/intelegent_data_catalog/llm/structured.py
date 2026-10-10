"""JSON schema output for models that support it.

A tool call lets Qwen think until the answer is empty.
"""

from pydantic_ai import NativeOutput
from pydantic_ai.models import Model


def structured_output[OutputT](
    model: Model,
    output: type[OutputT],
) -> NativeOutput[OutputT] | type[OutputT]:
    """Prefer JSON schema. Fall back to a tool call when the model cannot emit a schema."""
    if bool(model.profile.get("supports_json_schema_output", False)):
        return NativeOutput(output)
    return output

