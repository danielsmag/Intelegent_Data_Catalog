Register Airflow
================

The Airflow page registers Airflow 3 API servers. The catalog calls
``POST /auth/token``, then ``/api/v2``. A sync stores a DAG snapshot in
Postgres and links DAGs to catalog tables from asset URIs, source text, and
manual links.

From the Compose API container, add the base URL
``http://airflow-apiserver:8080``. The Airflow Compose project joins the
catalog network ``intelegent-data-catalog_default``. From a host
``uv run``, use ``http://localhost:8082``. The published host port is
``AIRFLOW_UI_PORT`` (default ``8082``).

The local Airflow user is ``admin`` and the password is ``airflow``. The
password is stored unencrypted in ``airflow_instances``. The API never
returns it.

``[airflow] timeout_seconds`` bounds each REST call. Instance URL and
password are per instance, not in ``settings.toml``.

After the instance exists, sync it from the UI or
``POST /api/v1/airflow/instances/{instance_id}/sync``. An
:class:`~intelegent_data_catalog.assets.dsl.operators.airflow.AirflowOperator`
then points at that instance slug and a ``dag_id``.
