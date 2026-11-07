"""Rebuild an operator from its stored reference."""

from pydantic import ValidationError

from intelegent_data_catalog.agents.definitions.models import OperatorRef
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError
from intelegent_data_catalog.agents.dsl.operators.base import Operator
from intelegent_data_catalog.agents.dsl.operators.steps import BuildContext, Describe

_OPERATORS: dict[str, type[Operator]] = {
    cls.__name__: cls
    for cls in (
        BuildContext,
        Describe,
    )
}


def operator_from_ref(ref: OperatorRef) -> Operator:
    """Return the operator ``ref`` names. Unknown names and bad fields raise."""
    cls: type[Operator] | None = _OPERATORS.get(ref.type)
    if cls is None:
        raise AgentDefinitionError(f"unknown operator: {ref.type}")
    try:
        return cls.model_validate(ref.params)
    except ValidationError as err:
        raise AgentDefinitionError(f"invalid {ref.type} fields") from err
