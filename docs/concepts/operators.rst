Operators
=========

An operator is the object an agent file constructs. Construction records
the fields. It does not read the catalog, and it does not build context.

``to_source`` is the lookup key stored with the declaration. ``to_ref`` is
the class name plus those fields. An executor rebuilds the class with
:func:`~intelegent_data_catalog.agents.dsl.operators.registry.operator_from_ref`
and calls ``execute``. Unknown class names and invalid fields fail the run.

``execute`` always does the same two steps: ``build_context`` with this
operator's flags, then ``describe``. ``describe`` keeps a context a person
already wrote. The lifecycle around that call is in :doc:`agents`.

Import the names from ``intelegent_data_catalog.agents.dsl``. Class pages
are in :doc:`/reference/dsl`.

.. code-block:: python

   from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, IcebergTable

   agent = Agent(
       key="orders",
       source=IcebergTable(
           source_key="polaris.sales.orders",
           data_source="Polaris",
       ),
       operators=[BuildContext(include_lineage=True), Describe()],
       schedule="0 2 * * *",
   )

Table operators
---------------

:class:`~intelegent_data_catalog.agents.dsl.IcebergTable`
and
:class:`~intelegent_data_catalog.agents.dsl.PostgresTable`
share one field set. Both store a table source. The resolver loads
``CatalogTableRow`` rows whose ``source_key`` matches.

``data_source`` is the data source name, optional. Set it when the same
``source_key`` could exist under more than one data source. A blank name is
rejected at construction. An unknown name stays unresolved. More than one
matching table is an error. Zero matches stay unresolved until a later scan.

The class does not filter on ``table_format``. Pick the class that matches
the table you mean. The stored operator name is that class.

.. code-block:: python

   from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, PostgresTable

   agent = Agent(
       key="customers",
       source=PostgresTable(
           source_key="app.public.customers",
           data_source="App",
       ),
       operators=[BuildContext(), Describe()],
   )

Schema
------

:class:`~intelegent_data_catalog.agents.dsl.Schema` names every
catalog table in one physical catalog and namespace. The applier keeps one
``SCHEMA`` agent. It does not take ``catalog_tables.agent_id``, so an explicit
table declaration for the same table stays a separate agent. One run writes
one context on the schema agent.

.. code-block:: python

   from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, Schema

   agent = Agent(
       key="sales",
       source=Schema(
           catalog="iceberg",
           namespace="sales",
           data_source="lakehouse",
           include=["orders"],
           exclude=["returns"],
       ),
       operators=[BuildContext(), Describe()],
       schedule="0 2 * * *",
   )

``include`` limits the names. ``None`` and an empty list mean the whole
schema. ``exclude`` drops names after ``include``. A blank name or a repeated
name is rejected. ``data_source`` is optional and works like the table
sources. Zero matches, or an unknown data source, leave the agent
``UNRESOLVED``. A later scan tries again. Adding or removing a table does not
add or remove agents.

``Describe(skip_described=True)`` skips the model call when the agent already
has context. The run still records the describe step.

``AgentPack(max_files=40, single_active_prompt=True)`` caps agent files in
each layer. ``max_files`` is from 1 to 200. ``single_active_prompt`` false
sends every enabled active prompt, joined in name order.

Agent files live in the directory named like the declaration file, for
example ``agents/sales/agent_sales/`` next to ``agent_sales.py``. ``AGENTS.md``
in the parent directory is the outer layer. The sidecar file wins on the
same slug. Writes go to the sidecar unless the request names
``layer=directory``.

``BuildContext`` on a table or schema uses these flags when the file sets them:

.. list-table::
   :header-rows: 1
   :widths: 28 14 58

   * - Flag
     - Default
     - What the build includes
   * - ``include_graph``
     - ``True``
     - Neighboring tables from the catalog graph. Disabled relationships are left out.
   * - ``include_lineage``
     - ``True``
     - Upstream datasets, downstream datasets, and the jobs between them. A job edge is not a join.
   * - ``include_data_profile``
     - ``False``
     - A physical data profile. Skipped when the profiler is not configured.

Catalog columns and relationships are always part of the table snapshot.
The flags add graph, lineage, and profile on top of that.

Airflow
-------

:class:`~intelegent_data_catalog.agents.dsl.AirflowDag`
links one synced DAG. ``instance`` is the instance slug from the Airflow
page. ``dag_id`` is the DAG id. Register and sync the instance first. A
missing instance or DAG stays unresolved. See :doc:`/howto/airflow`.

.. code-block:: python

   from intelegent_data_catalog.agents.dsl import Agent, AirflowDag, BuildContext, Describe

   agent = Agent(
       key="orders-dag",
       source=AirflowDag(
           instance="warehouse",
           dag_id="orders",
       ),
       operators=[BuildContext(), Describe()],
   )

The build asks the instance agent about this DAG only.

.. list-table::
   :header-rows: 1
   :widths: 28 14 58

   * - Flag
     - Default
     - What the build includes
   * - ``include_source``
     - ``True``
     - The DAG source, clipped to the Airflow source limit.
   * - ``include_previous``
     - ``True``
     - Findings already saved for this DAG.

``describe`` sends the DAG id, description, source, and task ids, and stores
the business context on the agent.

Page
----

:class:`~intelegent_data_catalog.agents.dsl.Page`
links a workspace file. ``path`` is the tree path, such as
``Shared/notebooks/orders.ipynb`` or ``Users/<subject>/notes.md``.
``workspace`` is the workspace slug. It defaults to ``shared``.

The node must already exist and must be a file. A missing workspace or path
stays unresolved. A folder is an error. A file whose name ends in ``.ipynb``
becomes a ``NOTEBOOK`` agent. Any other file becomes a ``FILE`` agent.

.. code-block:: python

   from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, Page

   agent = Agent(
       key="orders-notebook",
       source=Page(
           path="Shared/notebooks/orders.ipynb",
           workspace="shared",
       ),
       operators=[BuildContext(include_outputs=True), Describe()],
   )

The build reads the object bytes and extracts text. Notebooks contribute
cell sources. HTML contributes visible text. Text files (Markdown, Python,
SQL, JSON, YAML, CSV, and the other text suffixes) contribute their body,
clipped at 20,000 characters. Other types contribute no body.

.. list-table::
   :header-rows: 1
   :widths: 28 14 58

   * - Flag
     - Default
     - What the build includes
   * - ``include_outputs``
     - ``False``
     - Notebook cell outputs in the extracted text.
   * - ``include_table_links``
     - ``True``
     - Catalog tables named in the text.

Table names are taken from ``spark.table`` and ``read.table`` calls, from
``FROM`` and ``JOIN``, and from ``catalog.namespace.table`` paths. A name
matches a catalog table when the table name is the same, ignoring case.

``describe`` always reads notebook outputs, even when ``include_outputs``
is false on the build. It still keeps a context a person already wrote.
