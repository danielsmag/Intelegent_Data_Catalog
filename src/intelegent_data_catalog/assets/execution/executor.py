"""Local and Celery executors for one asset operator workload."""

from collections.abc import Callable
from typing import Protocol

from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.jobs.names import RUN_ASSET_OPERATOR


class OperatorExecutor(Protocol):
    """Submits one operator run. Local runs it; Celery queues it."""

    def submit(self, workload: OperatorWorkload) -> None:
        """Run or enqueue ``workload``. The asset row is already committed."""


class LocalOperatorExecutor:
    """Runs the workload in this process. For dev and tests."""

    def __init__(self, run: Callable[[OperatorWorkload], None]) -> None:
        self._run: Callable[[OperatorWorkload], None] = run

    def submit(self, workload: OperatorWorkload) -> None:
        """Run ``workload`` inline."""
        self._run(workload)


class CeleryOperatorExecutor:
    """Sends the workload to the catalog worker."""

    def __init__(self, send: Callable[[str], None] | None = None) -> None:
        self._send: Callable[[str], None] = _celery_send if send is None else send

    def submit(self, workload: OperatorWorkload) -> None:
        """Queue ``workload`` as JSON."""
        self._send(workload.model_dump_json())


def _celery_send(payload: str) -> None:
    from intelegent_data_catalog.jobs.celery_app import celery_app

    celery_app.tasks[RUN_ASSET_OPERATOR].delay(payload)
