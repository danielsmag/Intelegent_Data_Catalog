"""Celery task names shared by the client and the worker."""

RUN_CATALOG_JOB: str = "intelegent_data_catalog.jobs.tasks.run_catalog_job"
SYNC_TRINO_BINDING: str = "intelegent_data_catalog.jobs.tasks.sync_trino_binding"
SYNC_AIRFLOW_INSTANCE: str = "intelegent_data_catalog.jobs.tasks.sync_airflow_instance"
DISPATCH_DUE_SYNCS: str = "intelegent_data_catalog.jobs.tasks.dispatch_due_syncs"
