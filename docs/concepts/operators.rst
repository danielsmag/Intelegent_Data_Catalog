Operators
=========

An operator is the object an asset file constructs. Construction records
the fields. It does not read the catalog, and it does not build context.

``to_source`` is the lookup key stored with the declaration. ``to_ref`` is
the class name plus those fields. An executor rebuilds the class with
:func:`~intelegent_data_catalog.assets.dsl.operators.registry.operator_from_ref`
and calls ``execute``. Unknown class names and invalid fields fail the run.

``execute`` always does the same two steps: ``build_context`` with this
operator's flags, then ``describe``. ``describe`` keeps a context a person
already wrote. The lifecycle around that call is in :doc:`assets`.

Import the names from ``intelegent_data_catalog.assets.dsl``. Class pages
are in :doc:`/reference/dsl`.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator

   asset = Asset(
       key="orders",
       operator=IcebergTableOperator(
           source_key="polaris.sales.orders",
           data_source="Polaris",
           include_lineage=True,
       ),
       schedule="0 2 * * *",
   )

Table operators
---------------

:class:`~intelegent_data_catalog.assets.dsl.IcebergTableOperator`
and
:class:`~intelegent_data_catalog.assets.dsl.PostgresTableOperator`
share one field set. Both store a table source. The resolver loads
``CatalogTableRow`` rows whose ``source_key`` matches.

``data_source`` is the data source name, optional. Set it when the same
``source_key`` could exist under more than one data source. A blank name is
rejected at construction. An unknown name stays unresolved. More than one
matching table is an error. Zero matches stay unresolved until a later scan.

The class does not filter on ``table_format``. Pick the class that matches
the table you mean. The stored operator name is that class.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import Asset, PostgresTableOperator

   asset = Asset(
       key="customers",
       operator=PostgresTableOperator(
           source_key="app.public.customers",
           data_source="App",
       ),
   )

Schema
------

:class:`~intelegent_data_catalog.assets.dsl.SchemaOperator` names every
catalog table in one physical catalog and namespace. The applier keeps one
parent item with no asset, plus one child item per table. The child key is
``<key>/<table name>``. Each child is its own ``TABLE`` asset. It does not
take ``catalog_tables.asset_id``, so an explicit table declaration for the
same table stays a separate asset with its own context, runs, and agent files.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import Asset, SchemaOperator

   asset = Asset(
       key="sales",
       operator=SchemaOperator(
           catalog="iceberg",
           namespace="sales",
           data_source="lakehouse",
           include=["orders"],
           exclude=["returns"],
       ),
       schedule="0 2 * * *",
   )

``include`` limits the names. ``None`` and an empty list mean the whole
schema. ``exclude`` drops names after ``include``. A blank name or a repeated
name is rejected. ``data_source`` is optional and works like the table
operators. Zero matches, or an unknown data source, leave the parent
``UNRESOLVED``. A later scan tries again. An applied schema is resolved again
on every scan, so a new table becomes a child and a removed table drops its
child.

``describe`` defaults to true. ``skip_described`` defaults to true, so a child
that already has context skips the model call. The run still records the
describe step.

``pack_max_files`` (default 40, from 1 to 200) caps agent files in each layer.
``pack_single_active_prompt`` (default true) rejects a second active prompt in
the same layer. Set it false and every enabled active prompt is sent, joined
in name order.

Agent files for a schema live beside ``asset_<key>.py``. Each child can add
``asset_<table>/`` in that same directory. The ``asset_`` marker is required.
A folder without it is ignored. The folder is not created until something is
written there. Files merge by slug: the table file replaces the schema file.
An overlay rule with ``enabled: false`` turns that schema rule off for that
table only. An overlay ``AGENTS.md`` replaces the schema card. Writes from a
child go to ``asset_<table>/`` unless the request names ``layer=schema``.

``IcebergTableOperator`` and ``PostgresTableOperator`` default to the table build flags:

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

:class:`~intelegent_data_catalog.assets.dsl.AirflowOperator`
links one synced DAG. ``instance`` is the instance slug from the Airflow
page. ``dag_id`` is the DAG id. Register and sync the instance first. A
missing instance or DAG stays unresolved. See :doc:`/howto/airflow`.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import AirflowOperator, Asset

   asset = Asset(
       key="orders-dag",
       operator=AirflowOperator(
           instance="warehouse",
           dag_id="orders",
       ),
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
the business context on the asset.

Page
----

:class:`~intelegent_data_catalog.assets.dsl.PageOperator`
links a workspace file. ``path`` is the tree path, such as
``Shared/notebooks/orders.ipynb`` or ``Users/<subject>/notes.md``.
``workspace`` is the workspace slug. It defaults to ``shared``.

The node must already exist and must be a file. A missing workspace or path
stays unresolved. A folder is an error. A file whose name ends in ``.ipynb``
becomes a ``NOTEBOOK`` asset. Any other file becomes a ``FILE`` asset.

.. code-block:: python

   from intelegent_data_catalog.assets.dsl import Asset, PageOperator

   asset = Asset(
       key="orders-notebook",
       operator=PageOperator(
           path="Shared/notebooks/orders.ipynb",
           workspace="shared",
           include_outputs=True,
       ),
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
