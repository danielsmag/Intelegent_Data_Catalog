"""Celery application. Beat has one tick; sync and context schedules live in Postgres."""

from datetime import timedelta

from celery import Celery
from celery.signals import worker_init, worker_process_init

from intelegent_data_catalog.core.settings import AppSettings, load_settings
from intelegent_data_catalog.jobs.names import DISPATCH_DUE_SYNCS

_settings: AppSettings = load_settings()

app: Celery = Celery("intelegent_data_catalog", broker=_settings.celery.broker_url)
celery_app: Celery = app
app.conf.task_ignore_result = True
app.conf.task_store_eager_result = False
app.conf.task_always_eager = _settings.celery.task_always_eager
app.conf.task_eager_propagates = False
app.conf.broker_connection_retry_on_startup = True
app.conf.worker_send_task_events = True
app.conf.task_send_sent_event = True
app.conf.beat_schedule = {
    "dispatch-due-syncs": {
        "task": DISPATCH_DUE_SYNCS,
        "schedule": timedelta(seconds=_settings.celery.dispatch_interval_seconds),
    },
}


def _start_worker_container(**_kwargs: object) -> None:
    """Open a fresh container in this worker process."""
    from intelegent_data_catalog.api.container import AppContainer
    from intelegent_data_catalog.jobs.runtime import set_job_container

    container: AppContainer = AppContainer()
    container.logger()
    container.init_resources()
    set_job_container(container)


worker_init.connect(_start_worker_container)
worker_process_init.connect(_start_worker_container)


def _register_tasks() -> None:
    """Import task modules so the worker and eager mode can find them."""
    import intelegent_data_catalog.jobs.tasks as _tasks

    del _tasks


_register_tasks()
