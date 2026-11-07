Agents
======

An agent is the catalog row for one source that already exists: a catalog
table, a synced Airflow DAG, or a workspace file. A definition file does not
create that source. It names the source with an operator. The scanner links
the name to the row. A later operator run builds the knowledge context and,
unless a person already wrote it, the business context.

See :doc:`model` for how those names differ, :doc:`operators` for each
operator, and :doc:`execution` for when a run is queued.

Declaration
-----------

Write ``agent = Agent(...)`` in a file under ``agents/``. The constructor
registers the declaration with that file. It does not read the catalog.

``key`` is the stable id and the visible name. It must start with a letter or
digit and may then use letters, digits, ``_``, and ``-``, up to 255
characters. The same key in a later scan updates that declaration. There is
no separate name argument.

``operator`` names the source and the flags used when the agent runs. The
other arguments are optional overlays. Omit one and the stored value stays
as it is. Pass ``None`` for ``description`` or ``context`` to clear it.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Argument
     - Effect
   * - ``description``
     - Short description stored on the agent.
   * - ``context``
     - Business context written by a person. The operator will not replace it.
   * - ``status``
     - ``ACTIVE``, ``MISSING``, or ``DEPRECATED``.
   * - ``properties``
     - Extra JSON stored under ``properties.declared``.
   * - ``tags``
     - Labels stored on ``properties.tags`` and returned as ``tags``. Omit the argument and the stored labels stay. ``[]`` clears them. Each tag starts with a letter or digit and may use letters, digits, spaces, ``_``, ``-``, and ``.``, up to 64 characters. At most 32 tags. Repeated labels are kept once.
   * - ``schedule``
     - Five-field cron, such as ``0 2 * * *``. Omit it to run once per change.

Every applied agent also stores ``properties.definition`` with the file key
and the declaration ``key``.

Kinds
-----

The operator class picks the lookup. The source row picks the agent kind.

.. list-table::
   :header-rows: 1
   :widths: 28 24 48

   * - Source
     - Agent kind
     - Source row
   * - ``IcebergTable``
     - ``TABLE``
     - Catalog table
   * - ``PostgresTable``
     - ``TABLE``
     - Catalog table
   * - ``Schema``
     - ``SCHEMA``
     - Catalog tables in one catalog and namespace
   * - ``AirflowDag``
     - ``AIRFLOW_DAG``
     - Synced DAG
   * - ``Page`` on a ``.ipynb`` file
     - ``NOTEBOOK``
     - Workspace file
   * - ``Page`` on any other file
     - ``FILE``
     - Workspace file

``IcebergTable`` and ``PostgresTable`` use the same lookup.
Use the source class that matches the table.
The lookup does not read ``table_format``.

From file to row
----------------

#. The scanner executes the file and collects each ``agent = Agent(...)``.
#. The applier asks the resolver for the source the operator named.
#. A missing table, data source, Airflow instance, DAG, workspace, or file
   stays ``UNRESOLVED``. A later scan tries again. Removing the declaration
   drops the previous claim.
#. A ``source_key`` that matches more than one table, or a workspace folder,
   is an ``ERROR``. Folders are not agents.
#. A single match creates the agent row if the source does not have one yet,
   then writes the overlays the file passed.
#. If that declaration changed and it has an operator, the applier queues
   one run.

What a run writes
-----------------

The executor rebuilds the operator from the stored class name and fields,
then calls ``execute``. ``execute`` does two steps.

``build_context`` loads a knowledge snapshot for that agent kind. A table
agent stores the full table snapshot on the agent. The operator's
``include_*`` fields are the flags. Saved model and effort on the agent still
apply. See :doc:`operators` for what each flag includes.

``describe`` asks the model for business context and stores it with origin
``LLM``. Two cases skip that write:

* The declaration passed ``context``. That text is stored with origin
  ``USER``.
* Someone later edited the context in the UI. That edit is also ``USER``.

Pass ``context=None`` in the file to clear both the text and the origin.
The next run can write context again. A blank string clears it the same way.

When it runs again
------------------

Omit ``schedule`` and the operator runs when the declaration is applied.
Nothing is queued again until the file changes.

With a cron, the finish time of a run sets the next fire time. A later tick
queues another run after that time, and only after the previous run has
finished. A queued or running item is left alone. The executor
(``celery`` or ``local``) is in :doc:`execution`.
