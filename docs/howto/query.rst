Query tables
=============

Sample Data and the Query view run Spark SQL. Compose service ``spark-sql``
keeps a Spark 4.1.3 session connected to Polaris. The catalog API accepts one
read-only statement and sends it to that session.

The seed job uses the same Spark image and Iceberg 1.11. The first query
after Spark starts takes longer while the session warms up. Later queries
reuse that session.

``POST /api/v1/query`` takes the statement. Limits live in ``[query]``:
``spark_url`` (default ``http://127.0.0.1:8091``), ``max_query_rows``,
``max_sql_chars``, and ``query_timeout_seconds``.

The physical Polaris browser and this query route are not filtered by the
active workspace. When auth is enabled they still require a bearer token.
See :doc:`/reference/rest`.
