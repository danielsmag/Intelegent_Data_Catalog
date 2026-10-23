Configuration
=============

``settings.toml`` at the project root holds the process settings.
``CATALOG_SETTINGS_FILE`` selects a different file. Environment variables with
the ``CATALOG_`` prefix override the file. Nested keys use a double
underscore.

.. code-block:: bash

   CATALOG_APP__NAME=catalog
   CATALOG_APP__DEBUG=true
   CATALOG_SCAN__INTERVAL_SECONDS=30

``CATALOG_APP__DEBUG=true`` forces the logger to DEBUG and uses
``debug_format``.

Sections you set most often:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Section
     - Role
   * - ``app``
     - Process name and the debug flag.
   * - ``logging``
     - Level, format, console, and optional log file.
   * - ``api``
     - Bind host and port. Defaults are ``127.0.0.1`` and ``8090``.
   * - ``query``
     - Spark SQL URL and row, length, and timeout limits.
   * - ``database``
     - Postgres URL. ``auto_migrate`` applies Alembic on API startup when true.
   * - ``polaris``
     - Iceberg REST catalog URI, realm, catalog name, and client credentials.
   * - ``auth``
     - OIDC issuer, JWKS URL, and audience. Disabled uses the dev principal.
   * - ``object_store``
     - S3-compatible store for workspace files. An empty endpoint keeps files in memory.
   * - ``asset_definitions``
     - Bucket, prefix, and poll interval for definition files. See :doc:`/concepts/definitions`.
   * - ``celery``
     - Redis broker for background work.
   * - ``llm``
     - Provider, model, and ``CATALOG_LLM__API_KEY`` for context generation.
   * - ``observability``
     - OTLP export. A host run stays off until ``CATALOG_OBSERVABILITY__ENABLED=true``.
   * - ``notebook``
     - Kernel gateway for workspace notebooks.
   * - ``airflow``
     - Timeout for Airflow 3 REST calls. Instance URLs live in the catalog database.

When auth is enabled, ``issuer`` and ``jwks_url`` are required. When
observability is enabled, ``password`` is required.
