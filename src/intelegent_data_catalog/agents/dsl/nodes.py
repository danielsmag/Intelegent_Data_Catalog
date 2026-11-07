"""Agent declarations. Constructing one registers it with the open file."""

import re

from pydantic import JsonValue, ValidationError

from intelegent_data_catalog.agents.definitions.models import AgentSpec, PackSpec
from intelegent_data_catalog.agents.dsl.collector import current_collector
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError
from intelegent_data_catalog.agents.dsl.operators.base import Operator
from intelegent_data_catalog.agents.dsl.operators.steps import BuildContext
from intelegent_data_catalog.agents.dsl.pack import AgentPack
from intelegent_data_catalog.agents.dsl.sources import Source
from intelegent_data_catalog.persistence.enums import AgentStatus
from intelegent_data_catalog.registry.identifiers import require_slug
from intelegent_data_catalog.schedual.cron import parse_schedule

_KEY: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,254}$")
_TAG: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_NAME_LIMIT: int = 255
_TAG_LIMIT: int = 32


class _UnsetType:
    """Marks an argument the caller did not pass."""


_UNSET: _UnsetType = _UnsetType()


class Agent:
    """Declare one agent, the source it stands for, and the steps that run on it.

    Assign the object to ``agent``.
    ``key`` is the stable id and the visible name. There is no separate name argument.
    ``operators`` run in order. Omit them and the agent is resolved but nothing runs.
    ``schedule`` is a five-field cron; omit it to run once when the agent is first applied.
    ``context`` is a manual override. Without it, a ``Describe`` step writes the context.
    ``tags`` is a list of labels. Omit it and the stored tags stay. Pass ``[]`` to clear them.
    """

    def __init__(
        self,
        key: str,
        source: Source,
        *,
        operators: list[Operator] | None = None,
        agent_pack: AgentPack | None = None,
        description: str | _UnsetType | None = _UNSET,
        context: str | _UnsetType | None = _UNSET,
        status: AgentStatus | _UnsetType | None = _UNSET,
        properties: dict[str, JsonValue] | _UnsetType = _UNSET,
        tags: list[str] | _UnsetType | None = _UNSET,
        schedule: str | _UnsetType | None = _UNSET,
    ) -> None:
        steps: list[Operator] = [] if operators is None else list(operators)
        _check_steps(source, steps)
        status_value: AgentStatus | None = _status(status)
        property_value: dict[str, JsonValue] = _properties(properties)
        tag_value: list[str] = _tags(tags)
        schedule_value: str | None = _schedule(schedule)
        pack: PackSpec | None = None if agent_pack is None else PackSpec(
            max_files=agent_pack.max_files,
            single_active_prompt=agent_pack.single_active_prompt,
        )
        try:
            spec: AgentSpec = AgentSpec(
                key=_agent_key(key),
                source=source.to_ref(),
                operators=[step.to_ref() for step in steps],
                pack=pack,
                schedule=schedule_value,
                description=_optional_text(description, "description", required=False),
                context=_optional_text(context, "context", required=False),
                status=status_value,
                properties=property_value,
                tags=tag_value,
                set_fields=_set_fields(
                    (
                        ("description", description),
                        ("context", context),
                        ("status", status),
                        ("properties", properties),
                        ("tags", tags),
                    ),
                ),
            )
        except ValidationError as err:
            raise AgentDefinitionError("invalid agent") from err
        current_collector().add_agent(spec)


def _check_steps(source: Source, steps: list[Operator]) -> None:
    """Reject a repeated task and a build flag the source does not allow."""
    seen: set[str] = set()
    step: Operator
    for step in steps:
        if step.task_id in seen:
            raise AgentDefinitionError(f"operator {step.task_id} is listed twice")
        seen.add(step.task_id)
        if not isinstance(step, BuildContext):
            continue
        blocked: str
        for blocked in sorted(step.set_flag_names() - source.flag_names):
            raise AgentDefinitionError(f"{type(source).__name__} does not allow {blocked}")


def _schedule(value: str | _UnsetType | None) -> str | None:
    if isinstance(value, _UnsetType) or value is None:
        return None
    if not isinstance(value, str):
        raise AgentDefinitionError("schedule must be a cron string")
    return parse_schedule(value)


def _slug(value: str, field: str) -> str:
    try:
        return require_slug(value)
    except ValueError as err:
        raise AgentDefinitionError(f"{field}: {err}") from err


def _agent_key(value: str) -> str:
    if _KEY.fullmatch(value) is None:
        raise AgentDefinitionError(
            "agent key must start with a letter or digit and use letters, digits, '_' or '-'",
        )
    return value


def _optional_text(value: object, field: str, *, required: bool) -> str | None:
    if isinstance(value, _UnsetType):
        return None
    if value is None:
        if required:
            raise AgentDefinitionError(f"{field} cannot be null")
        return None
    if not isinstance(value, str):
        raise AgentDefinitionError(f"{field} must be a string")
    cleaned: str = value.strip()
    if cleaned == "":
        if required:
            raise AgentDefinitionError(f"{field} cannot be blank")
        return None
    if field == "name" and len(cleaned) > _NAME_LIMIT:
        raise AgentDefinitionError(f"{field} is longer than {_NAME_LIMIT} characters")
    return cleaned


def _status(value: AgentStatus | _UnsetType | None) -> AgentStatus | None:
    if isinstance(value, _UnsetType):
        return None
    if not isinstance(value, AgentStatus):
        raise AgentDefinitionError("status must be an agent status")
    return value


def _properties(value: dict[str, JsonValue] | _UnsetType) -> dict[str, JsonValue]:
    if isinstance(value, _UnsetType):
        return {}
    return dict(value)


def _tags(value: list[str] | _UnsetType | None) -> list[str]:
    if isinstance(value, _UnsetType) or value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AgentDefinitionError("tags must be a list of strings")
    found: list[str] = []
    seen: set[str] = set()
    item: object
    for item in value:
        if not isinstance(item, str):
            raise AgentDefinitionError("tags must be a list of strings")
        cleaned: str = item.strip()
        if _TAG.fullmatch(cleaned) is None:
            raise AgentDefinitionError(
                "each tag must start with a letter or digit and use letters, digits, "
                "spaces, '_', '-' or '.'",
            )
        if cleaned in seen:
            continue
        seen.add(cleaned)
        found.append(cleaned)
    if len(found) > _TAG_LIMIT:
        raise AgentDefinitionError(f"tags cannot list more than {_TAG_LIMIT} values")
    return found


def _set_fields(pairs: tuple[tuple[str, object], ...]) -> list[str]:
    found: list[str] = []
    for name, value in pairs:
        if not isinstance(value, _UnsetType):
            found.append(name)
    return found
