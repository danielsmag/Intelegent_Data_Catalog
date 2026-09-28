"""Seed Iceberg tables in Polaris and emit OpenLineage events."""

from pyspark.sql import SparkSession


def main() -> None:
    """Replace the local sales tables and build customer_spend from them."""
    spark: SparkSession = SparkSession.builder.getOrCreate()
    statements: list[str] = [
        "CREATE NAMESPACE IF NOT EXISTS sales",
        "DROP TABLE IF EXISTS sales.customer_spend",
        "DROP TABLE IF EXISTS sales.orders",
        "DROP TABLE IF EXISTS sales.customers",
        """
        CREATE TABLE sales.customers (
            customer_id BIGINT NOT NULL,
            name STRING NOT NULL,
            email STRING NOT NULL,
            city STRING NOT NULL
        ) USING iceberg
        """,
        """
        CREATE TABLE sales.orders (
            order_id BIGINT NOT NULL,
            customer_id BIGINT NOT NULL,
            order_date DATE NOT NULL,
            status STRING NOT NULL,
            amount DECIMAL(10, 2) NOT NULL
        ) USING iceberg
        """,
        """
        INSERT INTO sales.customers (customer_id, name, email, city) VALUES
            (1, 'Ada Lovelace', 'ada@example.com', 'London'),
            (2, 'Alan Turing', 'alan@example.com', 'Cambridge'),
            (3, 'Grace Hopper', 'grace@example.com', 'New York'),
            (4, 'Katherine Johnson', 'katherine@example.com', 'Hampton'),
            (5, 'Margaret Hamilton', 'margaret@example.com', 'Cambridge')
        """,
        """
        INSERT INTO sales.orders (order_id, customer_id, order_date, status, amount) VALUES
            (1001, 1, DATE '2026-01-12', 'delivered', CAST(120.50 AS DECIMAL(10, 2))),
            (1002, 1, DATE '2026-02-03', 'shipped', CAST(45.00 AS DECIMAL(10, 2))),
            (1003, 2, DATE '2026-02-18', 'placed', CAST(89.99 AS DECIMAL(10, 2))),
            (1004, 3, DATE '2026-03-01', 'delivered', CAST(210.00 AS DECIMAL(10, 2))),
            (1005, 3, DATE '2026-03-15', 'cancelled', CAST(15.25 AS DECIMAL(10, 2))),
            (1006, 4, DATE '2026-04-02', 'shipped', CAST(64.40 AS DECIMAL(10, 2))),
            (1007, 5, DATE '2026-04-20', 'placed', CAST(300.00 AS DECIMAL(10, 2))),
            (1008, 2, DATE '2026-05-05', 'delivered', CAST(18.75 AS DECIMAL(10, 2)))
        """,
        """
        CREATE TABLE sales.customer_spend
        USING iceberg
        AS
        SELECT
            c.customer_id,
            c.name,
            c.city,
            CAST(SUM(o.amount) AS DECIMAL(12, 2)) AS total_amount,
            COUNT(o.order_id) AS order_count
        FROM sales.customers AS c
        JOIN sales.orders AS o
            ON c.customer_id = o.customer_id
        GROUP BY c.customer_id, c.name, c.city
        """,
    ]
    statement: str
    for statement in statements:
        preview: str = " ".join(statement.split())
        print(f"sql {preview[:160]}")
        spark.sql(statement)

    customer_count: int = _count_rows(spark, "sales.customers")
    order_count: int = _count_rows(spark, "sales.orders")
    spend_count: int = _count_rows(spark, "sales.customer_spend")
    print(
        "seeded "
        f"sales.customers={customer_count} "
        f"sales.orders={order_count} "
        f"sales.customer_spend={spend_count}"
    )
    if customer_count != 5 or order_count != 8 or spend_count != 5:
        raise SystemExit("seed row counts did not match the expected sales sample")
    spark.stop()


def _count_rows(spark: SparkSession, table_name: str) -> int:
    row_count: int = int(spark.sql(f"SELECT COUNT(*) AS c FROM {table_name}").collect()[0]["c"])
    return row_count


if __name__ == "__main__":
    main()
