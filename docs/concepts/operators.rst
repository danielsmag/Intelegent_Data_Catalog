Operators
=========

An operator is one deterministic step on an asset. Construction records
the fields. ``execute`` runs later, after the asset row exists.

Agents no longer declare operators. An agent names a catalog source.
Context is built from the agent page, the API, or entity configuration.

Import asset names from ``intelegent_data_catalog.assets.dsl``.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import (
       Asset,
       GitHubRepo,
       GitHubRepoSnapshot,
       IcebergTable,
       IcebergTableMetadata,
       IcebergTableProfile,
   )

   Asset(
       key="orders",
       source=IcebergTable(catalog="catalog", namespace="sales", table="orders"),
       operators=[IcebergTableMetadata(), IcebergTableProfile()],
       schedule="0 3 * * *",
   )

The steps are:

- ``AirflowMetadata`` writes Airflow instance metadata.
- ``IcebergTableMetadata`` and ``IcebergSchemaMetadata`` write Iceberg REST metadata.
- ``IcebergTableProfile`` and ``IcebergSchemaProfile`` add a Spark profile.
- ``GitHubRepoSnapshot`` writes repository markdown. A scheduled run skips
  when the branch head commit is unchanged. ``POST /assets/{id}/run`` with
  ``force`` true writes it again.

Each step stores markdown and a JSON sidecar under ``asset-outputs/``.
The asset page renders the markdown.
