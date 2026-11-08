Agent DSL
=========

Import these names from ``intelegent_data_catalog.agents.dsl``.
Constructing :class:`~intelegent_data_catalog.agents.dsl.nodes.Agent`
registers the declaration with the definition file that is executing.
Deterministic steps live on assets. See :doc:`/concepts/operators`.

.. currentmodule:: intelegent_data_catalog.agents.dsl

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   Agent
   AgentPack
   Source
   IcebergTable
   PostgresTable
   Schema
   AirflowDag
   AirflowInstance
   Page

Asset sources
-------------

Import asset sources and operators from ``intelegent_data_catalog.assets.dsl``.
See :doc:`/concepts/operators`.

- ``IcebergTable`` — one table.
- ``IcebergSchema`` — one namespace. ``include`` and ``exclude`` filter table names.
- ``IcebergCatalog`` — one catalog (database). ``include`` and ``exclude`` filter namespace names.
- ``IcebergTableProfile``, ``IcebergSchemaProfile``, ``IcebergCatalogProfile`` — Spark profiles.
