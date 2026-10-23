UI
==

The activity bar switches the sidebar. The editor opens the page for the
thing you select. Paths such as ``/query`` and ``/sources`` select the
matching view on their own.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - View
     - What it shows
   * - Workspace
     - Folder tree for the active workspace. ``Shared/`` and ``Users/<you>/`` live here. Files and notebooks open in the editor.
   * - Assets
     - Definition files under ``spaces/``, ``scopes/``, and ``assets/``.
   * - Spaces
     - Registry tree of spaces, scopes, and attached tables.
   * - Catalog
     - Physical catalogs, namespaces, and tables from the explorer.
   * - Airflow
     - Registered instances and their DAGs. Add an instance from this sidebar.
   * - Search
     - Catalog search.
   * - Sources
     - Data source records. A source record does not provision a Trino or Spark connector.
   * - Query
     - One read-only Spark SQL statement. See :doc:`/howto/query`.

The account menu opens workspace administration at ``/workspaces`` and signs
out when Keycloak is on. Host dev with auth disabled has no sign-out.
