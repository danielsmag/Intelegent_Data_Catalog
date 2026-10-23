Compose
=======

``docker compose up -d`` starts the local stack: Neo4j, Apache Polaris, the
Polaris console, Marquez, OpenObserve, Keycloak, the catalog API, the catalog
UI, MinIO, and Celery Flower. The first start pulls images, builds the catalog
API and UI, builds the Polaris console, and downloads Spark's Maven packages.
``spark-lineage`` exits after it loads the sample tables and Marquez has the
``customer_spend`` job.

.. code-block:: bash

   cp .env.example .env
   docker compose up -d
   docker compose logs -f spark-lineage

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Surface
     - Address
   * - Neo4j Browser
     - http://localhost:7474
   * - Neo4j Bolt
     - bolt://localhost:7687
   * - Polaris Iceberg REST
     - http://localhost:8181/api/catalog
   * - Polaris management
     - http://localhost:8181/api/management/v1
   * - Polaris Console
     - http://localhost:8080
   * - Polaris health
     - http://localhost:8182/q/health
   * - MinIO API
     - http://localhost:9000
   * - MinIO console
     - http://localhost:9001
   * - Marquez API
     - http://localhost:5050
   * - Marquez lineage UI
     - http://localhost:3000
   * - Catalog API
     - http://localhost:8090
   * - Catalog UI
     - http://localhost:5173
   * - Keycloak
     - http://localhost:8180
   * - Celery Flower
     - http://localhost:5555
   * - OpenObserve
     - http://localhost:5080

MinIO is the S3 warehouse (bucket ``warehouse``). Spark inside Compose uses
``http://minio:9000``. Host clients use ``http://localhost:9000``.

Host port ``5050`` is the Marquez API so it does not collide with macOS
AirPlay on port ``5000``. Inside Compose, Spark still posts lineage to
``http://marquez:5000``.

Local defaults
--------------

These values are the Compose defaults for a laptop. Replace them outside this
stack.

Neo4j reads ``NEO4J_USER`` and ``NEO4J_PASSWORD`` from ``.env``. The defaults
are ``neo4j`` / ``password``.

Polaris root client defaults are ``root`` / ``s3cr3t`` in realm ``POLARIS``.
The catalog name default is ``catalog``. The MinIO warehouse credentials are
``polaris_root`` / ``polaris_pass``. Spark's generated principal is written to
``.local/polaris/client.env``, which is gitignored.

OpenObserve reads ``OPENOBSERVE_USER`` and ``OPENOBSERVE_PASSWORD`` from
``.env``. The defaults are ``root@example.com`` / ``Local-observe-1``. Spans
include prompts and tool arguments. Vector ships other Compose containers'
stdout into the ``containers`` log stream.

Keycloak's master admin reads ``KEYCLOAK_ADMIN`` and
``KEYCLOAK_ADMIN_PASSWORD`` from ``.env``. The defaults are ``admin`` /
``admin``. Catalog sign-in is covered in :doc:`/howto/sign-in`.

Sample tables
-------------

The seed job writes ``sales.customers`` and ``sales.orders``, then builds
``sales.customer_spend`` from that join. OpenLineage records the join in
Marquez. Search the lineage UI for ``customer_spend``. A later
``docker compose up -d`` leaves those tables in place when that lineage edge
already exists. To drop and reload them:

.. code-block:: bash

   SEED_FORCE=true docker compose up -d --force-recreate spark-lineage

``docker compose down`` keeps volumes. ``docker compose down -v`` deletes
Neo4j, Polaris, MinIO, Marquez, OpenObserve, the Vector checkpoint, and the
Spark package cache.

``CATALOG_UI_PORT`` and ``CATALOG_API_PORT`` change the published UI and API
ports. Inside the API container, ``CATALOG_API__HOST`` is ``0.0.0.0`` and
``CATALOG_POLARIS__*`` points at the Compose network.
