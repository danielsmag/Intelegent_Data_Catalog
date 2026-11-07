Asset DSL
=========

Import these names from ``intelegent_data_catalog.assets.dsl``.
Constructing :class:`~intelegent_data_catalog.assets.dsl.nodes.Space`,
:class:`~intelegent_data_catalog.assets.dsl.nodes.Scope`, or
:class:`~intelegent_data_catalog.assets.dsl.nodes.Asset` registers the
declaration with the definition file that is executing. Operators record
their fields at construction and run in :doc:`/concepts/execution`.

.. currentmodule:: intelegent_data_catalog.assets.dsl

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   Space
   Scope
   Asset
   Operator
   OperatorContext
   TableOperator
   IcebergTableOperator
   PostgresTableOperator
   SchemaOperator
   AirflowOperator
   PageOperator
   operator_from_ref
