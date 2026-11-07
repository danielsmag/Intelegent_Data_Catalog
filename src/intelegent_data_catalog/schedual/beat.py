"""Beat entries. Due work itself lives in Postgres."""

from datetime import timedelta

from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.jobs.names import DISPATCH_DUE_SYNCS, SCAN_AGENT_DEFINITIONS


def beat_schedule(settings: AppSettings) -> dict[str, dict[str, object]]:
    """Return the Celery Beat entries for this process."""
    schedule: dict[str, dict[str, object]] = {
        "dispatch-due-syncs": {
            "task": DISPATCH_DUE_SYNCS,
            "schedule": timedelta(seconds=settings.celery.dispatch_interval_seconds),
        },
    }
    if settings.agent_definitions.enabled:
        schedule["scan-agent-definitions"] = {
            "task": SCAN_AGENT_DEFINITIONS,
            "schedule": timedelta(seconds=settings.agent_definitions.poll_interval_seconds),
        }
    return schedule
