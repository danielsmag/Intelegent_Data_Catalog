UI
==

The activity bar switches the sidebar. The editor opens the page for the
thing you select. Paths such as ``/query`` and ``/search`` select the
matching view on their own. ``/sources`` opens Catalog.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - View
     - What it shows
   * - Workspace
     - Folder tree for the active workspace. ``Shared/`` and ``Users/<you>/`` live here. Files and notebooks open in the editor.
   * - Agents
     - Agents in the active workspace, and definition files under ``agents/``.
   * - Catalog
     - Physical catalogs, namespaces, and tables from the explorer.
   * - Search
     - Catalog search.
   * - Query
     - One read-only Spark SQL statement. See :doc:`/howto/query`.

The account menu opens workspace administration at ``/workspaces`` and signs
out when Keycloak is on. Host dev with auth disabled has no sign-out.
