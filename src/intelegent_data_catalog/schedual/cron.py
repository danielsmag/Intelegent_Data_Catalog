"""Five-field cron used as an agent operator schedule."""

from datetime import datetime

from croniter import croniter

from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError


def parse_schedule(value: str) -> str:
    """Return a trimmed five-field cron. Anything else raises."""
    cleaned: str = value.strip()
    if len(cleaned.split()) != 5:
        raise AgentDefinitionError("schedule must be a five-field cron")
    try:
        croniter(cleaned)
    except (ValueError, KeyError) as err:
        raise AgentDefinitionError(f"invalid schedule: {cleaned}") from err
    return cleaned


def next_cron(schedule: str, after: datetime) -> datetime:
    """Return the first fire time strictly after ``after``."""
    iterator: croniter = croniter(schedule, after)
    found: object = iterator.get_next(datetime)
    if not isinstance(found, datetime):
        raise AgentDefinitionError(f"invalid schedule: {schedule}")
    if found.tzinfo is None and after.tzinfo is not None:
        return found.replace(tzinfo=after.tzinfo)
    return found
