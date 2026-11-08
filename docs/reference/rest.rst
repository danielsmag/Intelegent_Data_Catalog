REST API
========

Routes hang off ``/api/v1``. Operation text comes from the route docstrings.
The schema below is the same document FastAPI builds from the router. The
docs build mounts that router on a bare application and does not open
Postgres.

The rendered reference is the `OpenAPI page <openapi.html>`_. Tags match the
groups below.

Auth
----

``[auth] enabled`` false uses the dev principal and skips bearer checks.
``require_auth`` then allows the request through. Enabled auth checks a
``Authorization: Bearer`` token against the issuer and JWKS URL.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Routes
     - Access
   * - ``GET /api/v1/health``
     - No access dependency.
   * - Platform, Airflow, schedules, configuration, registry, agents, agent definitions
     - Resolve the caller. Workspace membership gates workspace files and agents. The physical catalog is visible to a signed-in caller.
   * - Workspaces and workspace files
     - Resolve the caller.
   * - Notebook HTTP routes
     - Resolve the caller on each route. The kernel websocket is authorized by a ticket.
   * - Catalog explorer and ``POST /api/v1/query``
     - Bearer token when auth is enabled. These routes do not apply the workspace visibility check.

``X-Workspace-Id`` selects the workspace. See :doc:`/howto/sign-in`.

Assets
------

Profile reads are signed in, like the rest of ``/api/v1/assets``.

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - Route
     - Result
   * - ``GET /api/v1/assets/{asset_id}/profile``
     - Newest profile document and markdown. 404 when the asset has no profile output.
   * - ``GET /api/v1/assets/{asset_id}/profile/graph``
     - Neo4j profile subgraph for that asset. 503 when the projection is not configured.
