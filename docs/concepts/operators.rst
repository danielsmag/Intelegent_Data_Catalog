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
       IcebergCatalog,
       IcebergCatalogProfile,
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
   Asset(
       key="sales_catalog",
       source=IcebergCatalog(catalog="catalog", include=["sales"]),
       operators=[IcebergCatalogProfile(max_tables=10)],
       schedule="0 4 * * *",
   )

The steps are:

- ``AirflowMetadata`` writes Airflow instance metadata.
- ``IcebergTableMetadata``, ``IcebergSchemaMetadata``, and
  ``IcebergCatalogMetadata`` write Iceberg REST metadata.
- ``IcebergTableProfile``, ``IcebergSchemaProfile``, and
  ``IcebergCatalogProfile`` add a Spark profile. A schema or catalog profile
  also infers candidate keys, candidate references, and same-name columns.
- ``GitHubRepoSnapshot`` writes repository markdown. A scheduled run skips
  when the branch head commit is unchanged. ``POST /assets/{id}/run`` with
  ``force`` true writes it again.

``IcebergCatalog`` names one Polaris catalog. ``include`` and ``exclude``
filter namespace names the same way a schema asset filters tables.

Each step stores markdown and a JSON sidecar under ``asset-outputs/``.
A profile step also replaces that asset's Neo4j subgraph
(``projection = 'profile'``, owned by the asset id) when Neo4j is enabled.
A graph outage is recorded on the profile document and the run still succeeds.

``GET /api/v1/assets/{asset_id}/profile`` returns the newest profile document.
``GET /api/v1/assets/{asset_id}/profile/graph`` returns the subgraph.
The asset page renders both.
