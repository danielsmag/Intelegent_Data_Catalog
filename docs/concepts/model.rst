Catalog model
=============

Five names show up everywhere. They are different objects.

Workspace
---------

A workspace is the sign-in boundary. Every user belongs to the built-in
**Shared** workspace. Platform admins create more workspaces and add members
by email after that person has signed in once.

The UI sends the active workspace as ``X-Workspace-Id``. Workspace files live
in the MinIO bucket ``workspace``. Postgres stores the folder tree
(``Shared/`` and ``Users/<you>/``). ``Users/<you>/`` is visible to that user
and to platform admins.

Space
-----

A space is a catalog domain, such as ``analytics``. It is not a workspace.

``OPEN`` spaces are visible from every workspace. ``ISOLATED`` spaces are
visible only from workspaces bound to them. A definition file creates a
missing space as ``OPEN``. The DSL has no isolation argument; change isolation
from the UI or the spaces API.

Scope
-----

A scope sits inside one space. Definitions name the parent by space slug.
Tables and assets can be attached to a scope after they exist.

Asset
-----

An asset is one catalog object with a stable ``key``: a table, an Airflow DAG,
a notebook, or a file. The definition names an operator. The operator names
the source. The asset is not placed in a space or scope by the definition.
Membership is a separate attachment.

``context`` on the declaration is a manual override. Leave it unset and the
operator writes the context when it runs. A later manual edit is kept.
How that path works is in :doc:`assets` and :doc:`operators`.

Physical table
--------------

Polaris stores Iceberg tables. Spark SQL reads them. The registry may also
record a catalog table, its columns, and a data source. A data source row
does not provision a Trino or Spark connector. The physical browser and
``POST /api/v1/query`` are not filtered by workspace.
