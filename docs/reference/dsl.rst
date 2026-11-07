Agent DSL
=========

Import these names from ``intelegent_data_catalog.agents.dsl``.
Constructing :class:`~intelegent_data_catalog.agents.dsl.nodes.Agent`
registers the declaration with the definition file that is executing.
Operators record their fields at construction and run in
:doc:`/concepts/execution`.

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
   Operator
   OperatorContext
   BuildContext
   Describe
   operator_from_ref
