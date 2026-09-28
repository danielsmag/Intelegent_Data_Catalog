# Intelegent Data Catalog

Python 3.14 application for an intelligent data catalog.

## Setup

```bash
uv sync --group dev
```

## Run

```bash
uv run intelegent-data-catalog
```

`settings.toml` holds the app name, logger options, and scan interval. Environment variables with the `CATALOG_` prefix override that file (`CATALOG_APP__NAME`, `CATALOG_APP__DEBUG`, `CATALOG_SCAN__INTERVAL_SECONDS`). `CATALOG_APP__DEBUG=true` forces the logger to DEBUG and uses `debug_format`.

The process scans the `db` folder for Iceberg tables on the `[scan] interval_seconds` cadence. The scan only reads the folder.

## Neo4j, Polaris, and OpenLineage

```bash
cp .env.example .env
docker compose up -d
docker compose logs -f spark-lineage
```

`docker compose up -d` starts Neo4j, the Apache Polaris Iceberg catalog, Polaris Console, Marquez (the OpenLineage backend), the catalog API, and the catalog UI. The first start pulls images, builds Polaris Console from `apache/polaris-tools`, builds the catalog API and UI images, and downloads Maven packages for Spark. `spark-lineage` exits after it loads the sample tables and Marquez has the `customer_spend` job.

| Surface | Address |
| --- | --- |
| Neo4j Browser | http://localhost:7474 |
| Neo4j Bolt | bolt://localhost:7687 |
| Polaris Iceberg REST | http://localhost:8181/api/catalog |
| Polaris management | http://localhost:8181/api/management/v1 |
| Polaris Console | http://localhost:8080 |
| Polaris health | http://localhost:8182/q/health |
| MinIO API | http://localhost:9000 |
| MinIO console | http://localhost:9001 |
| Marquez API | http://localhost:5050 |
| Marquez lineage UI | http://localhost:3000 |
| Catalog API | http://localhost:8090 |
| Catalog UI | http://localhost:5173 |

MinIO is the S3 warehouse (`warehouse` bucket). Official `minio/minio` images are not anonymously pullable, so Compose pins `elestio/minio` at a digest. Spark in Compose talks to `http://minio:9000`; host clients use `http://localhost:9000`.

Host port `5050` is the Marquez API so it does not collide with macOS AirPlay on port `5000`. Inside Compose, Spark still posts lineage to `http://marquez:5000`.

Neo4j login uses `NEO4J_USER` and `NEO4J_PASSWORD` from `.env`. Defaults are `neo4j` / `password`.

Polaris root client defaults are `root` / `s3cr3t` in realm `POLARIS`. Catalog name default is `catalog`. Open Polaris Console at http://localhost:8080 and sign in with those client credentials. Table files live in the MinIO bucket `warehouse` (`polaris_root` / `polaris_pass`). Spark uses a generated principal; its client id and secret are written to `.local/polaris/client.env` (gitignored).

The seed job writes `sales.customers` and `sales.orders`, then builds `sales.customer_spend` from that join. OpenLineage records that join in Marquez. Open the lineage UI and search for `customer_spend`. A later `docker compose up -d` leaves those tables in place when that lineage edge already exists. To drop and reload them:

```bash
SEED_FORCE=true docker compose up -d --force-recreate spark-lineage
```

`docker compose down` keeps volumes. `docker compose down -v` deletes Neo4j, Polaris, MinIO, Marquez, and the Spark package cache.

## Catalog UI

The UI container serves the built explorer and proxies `/api` to the catalog API. That API calls Polaris at `http://polaris:8181`. `CATALOG_UI_PORT` and `CATALOG_API_PORT` change the published ports. Inside the API container, `CATALOG_API__HOST` is `0.0.0.0` and `CATALOG_POLARIS__*` points at the Compose network.

The Spark seed must have run so `sales.customers`, `sales.orders`, and `sales.customer_spend` exist. For a host-only dev server instead of the UI container, run `cd web && npm install && npm run dev`. That Vite server proxies `/api` to `http://127.0.0.1:8090`.

## Querying tables

Sample Data and the Query view run Spark SQL. Compose service `spark-sql` keeps a Spark 4.1.3 session connected to Polaris. The catalog API accepts one read-only statement, then sends it to that session. The seed job uses the same Spark image and Iceberg 1.11. The first query after Spark starts takes longer while the session warms up. Later queries reuse that session.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy
```
