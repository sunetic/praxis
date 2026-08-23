\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DROP SCHEMA IF EXISTS commerce CASCADE;
DROP SCHEMA IF EXISTS operations CASCADE;
CREATE SCHEMA commerce;
CREATE SCHEMA operations;

SELECT setseed(0.20260820);

CREATE TABLE commerce.customers (
    customer_id bigint PRIMARY KEY,
    email text NOT NULL,
    segment text NOT NULL,
    region text NOT NULL,
    created_at timestamptz NOT NULL,
    marketing_opt_in boolean NOT NULL,
    profile jsonb NOT NULL
);

INSERT INTO commerce.customers
SELECT
    g,
    'customer-' || g || '@example.test',
    CASE WHEN g % 10 < 2 THEN 'enterprise'
         WHEN g % 10 < 6 THEN 'growth'
         ELSE 'consumer' END,
    CASE WHEN g % 10 < 2 THEN 'APAC'
         WHEN g % 4 = 0 THEN 'EU'
         ELSE 'NA' END,
    now() - ((g % 1500) || ' days')::interval,
    g % 3 <> 0,
    jsonb_build_object('tier', 1 + g % 5, 'source', CASE WHEN g % 7 = 0 THEN 'partner' ELSE 'organic' END)
FROM generate_series(1, 60000) AS g;

CREATE TABLE commerce.orders (
    order_id bigint PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES commerce.customers(customer_id),
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    total_amount numeric(12,2) NOT NULL,
    sales_channel text NOT NULL,
    notes text
);

INSERT INTO commerce.orders
SELECT
    g,
    1 + ((g * 37) % 60000),
    CASE WHEN g % 100 < 58 THEN 'completed'
         WHEN g % 100 < 73 THEN 'pending'
         WHEN g % 100 < 86 THEN 'shipped'
         WHEN g % 100 < 95 THEN 'cancelled'
         ELSE 'refunded' END,
    now() - ((g % 365) || ' days')::interval - ((g % 86400) || ' seconds')::interval,
    now() - ((g % 364) || ' days')::interval,
    round((15 + (g % 25000) / 13.0)::numeric, 2),
    CASE WHEN g % 5 = 0 THEN 'marketplace' WHEN g % 3 = 0 THEN 'mobile' ELSE 'web' END,
    CASE WHEN g % 29 = 0 THEN repeat('manual-review;', 8) ELSE NULL END
FROM generate_series(1, 500000) AS g;

CREATE INDEX orders_customer_id_idx ON commerce.orders(customer_id);

CREATE TABLE commerce.order_items (
    item_id bigint PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES commerce.orders(order_id),
    sku text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(12,2) NOT NULL
);

INSERT INTO commerce.order_items
SELECT
    g,
    1 + ((g - 1) % 500000),
    'SKU-' || lpad(((g * 17) % 45000)::text, 6, '0'),
    1 + (g % 5),
    round((3 + (g % 5000) / 17.0)::numeric, 2)
FROM generate_series(1, 1200000) AS g;

CREATE INDEX order_items_order_id_idx ON commerce.order_items(order_id);

CREATE TABLE commerce.payments (
    payment_id bigint PRIMARY KEY,
    payment_reference text NOT NULL,
    order_id bigint,
    customer_id bigint,
    amount numeric(12,2) NOT NULL,
    currency text NOT NULL,
    payment_status text NOT NULL,
    paid_at timestamptz
);

INSERT INTO commerce.payments
SELECT
    g,
    'PAY-' || lpad((CASE WHEN g > 300000 AND g % 97 = 0 THEN g - 100000 ELSE g END)::text, 9, '0'),
    CASE WHEN g % 211 = 0 THEN 900000 + g ELSE 1 + ((g * 13) % 500000) END,
    CASE WHEN g % 101 = 0 THEN 70000 + g ELSE 1 + ((g * 19) % 60000) END,
    CASE WHEN g % 997 = 0 THEN -round((g % 1000 / 7.0)::numeric, 2)
         ELSE round((10 + (g % 18000) / 11.0)::numeric, 2) END,
    CASE WHEN g % 499 = 0 THEN 'US_DOLLAR' WHEN g % 11 = 0 THEN 'EUR' ELSE 'USD' END,
    CASE WHEN g % 601 = 0 THEN 'unknown' WHEN g % 17 = 0 THEN 'failed' ELSE 'settled' END,
    now() - ((g % 400) || ' days')::interval
FROM generate_series(1, 350000) AS g;

CREATE INDEX payments_reference_idx ON commerce.payments(payment_reference);
CREATE INDEX payments_reference_duplicate_idx ON commerce.payments(payment_reference);
CREATE INDEX payments_customer_idx ON commerce.payments(customer_id);

CREATE TABLE operations.audit_events (
    event_id bigint PRIMARY KEY,
    tenant_id integer NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL
);

INSERT INTO operations.audit_events
SELECT
    g,
    1 + (g % 80),
    CASE WHEN g % 50 = 0 THEN 'permission_denied'
         WHEN g % 17 = 0 THEN 'payment_retry'
         ELSE 'order_updated' END,
    now() - ((g % 120) || ' days')::interval - ((g % 86400) || ' seconds')::interval,
    'service-' || (g % 30),
    jsonb_build_object('order_id', 1 + g % 500000, 'request_id', encode(digest(g::text, 'sha256'), 'hex'))
FROM generate_series(1, 600000) AS g;

CREATE TABLE operations.work_queue (
    job_id bigint PRIMARY KEY,
    queue_name text NOT NULL,
    state text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL,
    payload text NOT NULL
) WITH (autovacuum_enabled = false);

INSERT INTO operations.work_queue
SELECT
    g,
    CASE WHEN g % 5 = 0 THEN 'billing' ELSE 'fulfillment' END,
    CASE WHEN g % 9 = 0 THEN 'failed' ELSE 'ready' END,
    g % 7,
    now() - ((g % 48) || ' hours')::interval,
    repeat('queue-payload-' || g || ';', 6)
FROM generate_series(1, 250000) AS g;

UPDATE operations.work_queue SET attempts = attempts + 1 WHERE job_id % 2 = 0;
UPDATE operations.work_queue SET state = 'retry' WHERE job_id % 3 = 0;
UPDATE operations.work_queue SET payload = payload || 'touched' WHERE job_id % 5 = 0;
DELETE FROM operations.work_queue WHERE job_id <= 70000;

CREATE TABLE operations.feature_flags (
    flag_key text PRIMARY KEY,
    enabled boolean NOT NULL,
    rollout_percent integer NOT NULL,
    updated_at timestamptz NOT NULL
);

INSERT INTO operations.feature_flags VALUES
('checkout_v2', true, 25, now()),
('invoice_async', false, 0, now()),
('fraud_model_v3', true, 70, now());

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_writer') THEN
        CREATE ROLE app_writer LOGIN PASSWORD 'writer-test-only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_auditor') THEN
        CREATE ROLE readonly_auditor LOGIN PASSWORD 'readonly-test-only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendor_support') THEN
        CREATE ROLE vendor_support LOGIN PASSWORD 'vendor-test-only' CREATEDB;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA commerce, operations TO app_writer, readonly_auditor, vendor_support;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA commerce TO app_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA commerce, operations TO readonly_auditor;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA commerce TO vendor_support;
GRANT CREATE ON SCHEMA public TO PUBLIC;

SELECT pg_stat_reset();
SELECT pg_stat_statements_reset();

ANALYZE commerce.customers;
ANALYZE commerce.orders;
ANALYZE commerce.order_items;
ANALYZE commerce.payments;
ANALYZE operations.audit_events;
ANALYZE operations.work_queue;
