"""Enqueue catalog work. Tests supply their own publisher."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.jobs.names import (
    RUN_CATALOG_JOB,
    SYNC_AIRFLOW_INSTANCE,
    SYNC_TRINO_BINDING,
)


class BuildJobPublisher(Protocol):
    """Sends one persisted catalog job to a worker."""

    def enqueue_build(self, job_id: UUID) -> None:
        """Queue a running catalog job. The row is already committed."""


class SyncJobPublisher(Protocol):
    """Sends one due sync to a worker."""

    def enqueue_trino_sync(self, source_binding_id: UUID) -> None:
        """Queue a Trino metadata sync."""

    def enqueue_airflow_sync(self, instance_id: UUID) -> None:
        """Queue an Airflow DAG snapshot sync."""


class CeleryJobPublisher:
    """Publishes tasks through the configured Celery app."""

    def enqueue_build(self, job_id: UUID) -> None:
        """Queue a running catalog job."""
        self._send(RUN_CATALOG_JOB, job_id)

    def enqueue_trino_sync(self, source_binding_id: UUID) -> None:
        """Queue a Trino metadata sync."""
        self._send(SYNC_TRINO_BINDING, source_binding_id)

    def enqueue_airflow_sync(self, instance_id: UUID) -> None:
        """Queue an Airflow DAG snapshot sync."""
        self._send(SYNC_AIRFLOW_INSTANCE, instance_id)

    def _send(self, task_name: str, subject_id: UUID) -> None:
        from intelegent_data_catalog.jobs.celery_app import celery_app

        # delay honors task_always_eager. send_task ignores it.
        celery_app.tasks[task_name].delay(str(subject_id))
