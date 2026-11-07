Catalog model
=============

Three names show up everywhere. They are different objects.

Workspace
---------

A workspace is the sign-in boundary. Every user belongs to the built-in
**Shared** workspace. Platform admins create more workspaces and add members
by email after that person has signed in once.

The UI sends the active workspace as ``X-Workspace-Id``. Workspace files live
in the MinIO bucket ``workspace``. Postgres stores the folder tree
(``Shared/`` and ``Users/<you>/``). ``Users/<you>/`` is visible to that user
and to platform admins.

Asset
-----

An asset is one catalog object with a stable ``key``: a table, an Airflow DAG,
a notebook, or a file. The definition names an operator. The operator names
the source. The asset belongs to a workspace.

``context`` on the declaration is a manual override. Leave it unset and the
operator writes the context when it runs. A later manual edit is kept.
How that path works is in :doc:`assets` and :doc:`operators`.

Physical table
--------------

Polaris stores Iceberg tables. Spark SQL reads them. The registry may also
record a catalog table, its columns, and a data source. A data source row
does not provision a Trino or Spark connector. The physical browser and
``POST /api/v1/query`` are not filtered by workspace.
