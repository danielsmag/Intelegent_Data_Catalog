CREATE SCHEMA IF NOT EXISTS sales;

CREATE TABLE IF NOT EXISTS sales.customers (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.orders (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES sales.customers (id),
    amount NUMERIC(10, 2) NOT NULL
);

INSERT INTO sales.customers (id, name) VALUES (1, 'Ada')
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales.orders (id, customer_id, amount) VALUES (10, 1, 12.50)
ON CONFLICT (id) DO NOTHING;
