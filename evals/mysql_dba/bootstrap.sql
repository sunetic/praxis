SET SESSION sql_mode = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';
SET SESSION time_zone = '+00:00';

DROP DATABASE IF EXISTS commerce;
DROP DATABASE IF EXISTS operations;
CREATE DATABASE commerce CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE operations CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE operations.digits (d TINYINT UNSIGNED PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO operations.digits VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9);

CREATE TABLE operations.numbers (n INT UNSIGNED PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO operations.numbers (n)
SELECT a.d + 10*b.d + 100*c.d + 1000*d.d + 10000*e.d + 100000*f.d + 1
FROM operations.digits a
CROSS JOIN operations.digits b
CROSS JOIN operations.digits c
CROSS JOIN operations.digits d
CROSS JOIN operations.digits e
CROSS JOIN operations.digits f
WHERE a.d + 10*b.d + 100*c.d + 1000*d.d + 10000*e.d + 100000*f.d < 600000;

CREATE TABLE commerce.customers (
    customer_id BIGINT UNSIGNED PRIMARY KEY,
    email VARCHAR(160) NOT NULL,
    segment VARCHAR(24) NOT NULL,
    region VARCHAR(16) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    marketing_opt_in BOOLEAN NOT NULL,
    profile JSON NOT NULL,
    KEY customers_created_at_idx (created_at)
) ENGINE=InnoDB;

INSERT INTO commerce.customers
SELECT
    n,
    CONCAT('customer-', n, '@example.test'),
    CASE WHEN MOD(n, 10) < 2 THEN 'enterprise'
         WHEN MOD(n, 10) < 6 THEN 'growth'
         ELSE 'consumer' END,
    CASE WHEN MOD(n, 10) < 2 THEN 'APAC'
         WHEN MOD(n, 4) = 0 THEN 'EU'
         ELSE 'NA' END,
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 1500) DAY,
    MOD(n, 3) <> 0,
    JSON_OBJECT('tier', 1 + MOD(n, 5), 'source', IF(MOD(n, 7) = 0, 'partner', 'organic'))
FROM operations.numbers
WHERE n <= 50000;

CREATE TABLE commerce.orders (
    order_id BIGINT UNSIGNED PRIMARY KEY,
    customer_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    sales_channel VARCHAR(24) NOT NULL,
    notes TEXT,
    KEY orders_customer_id_idx (customer_id),
    KEY orders_updated_at_idx (updated_at),
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id)
        REFERENCES commerce.customers(customer_id)
) ENGINE=InnoDB;

INSERT INTO commerce.orders
SELECT
    n,
    1 + MOD(n * 37, 50000),
    CASE WHEN MOD(n, 100) < 58 THEN 'completed'
         WHEN MOD(n, 100) < 73 THEN 'pending'
         WHEN MOD(n, 100) < 86 THEN 'shipped'
         WHEN MOD(n, 100) < 95 THEN 'cancelled'
         ELSE 'refunded' END,
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 365) DAY - INTERVAL MOD(n, 86400) SECOND,
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 364) DAY,
    ROUND(15 + MOD(n, 25000) / 13.0, 2),
    CASE WHEN MOD(n, 5) = 0 THEN 'marketplace'
         WHEN MOD(n, 3) = 0 THEN 'mobile'
         ELSE 'web' END,
    IF(MOD(n, 29) = 0, REPEAT('manual-review;', 8), NULL)
FROM operations.numbers
WHERE n <= 300000;

CREATE TABLE commerce.order_items (
    item_id BIGINT UNSIGNED PRIMARY KEY,
    order_id BIGINT UNSIGNED NOT NULL,
    sku VARCHAR(32) NOT NULL,
    quantity INT UNSIGNED NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    KEY order_items_order_id_idx (order_id),
    CONSTRAINT order_items_order_fk FOREIGN KEY (order_id)
        REFERENCES commerce.orders(order_id)
) ENGINE=InnoDB;

INSERT INTO commerce.order_items
SELECT
    n,
    1 + MOD(n - 1, 300000),
    CONCAT('SKU-', LPAD(MOD(n * 17, 45000), 6, '0')),
    1 + MOD(n, 5),
    ROUND(3 + MOD(n, 5000) / 17.0, 2)
FROM operations.numbers
WHERE n <= 600000;

CREATE TABLE commerce.payments (
    payment_id BIGINT UNSIGNED PRIMARY KEY,
    payment_reference VARCHAR(32) NOT NULL,
    order_id BIGINT UNSIGNED,
    customer_id BIGINT UNSIGNED,
    amount DECIMAL(12,2) NOT NULL,
    currency VARCHAR(16) NOT NULL,
    payment_status VARCHAR(20) NOT NULL,
    paid_at DATETIME(6),
    KEY payments_reference_idx (payment_reference),
    KEY payments_reference_lookup_idx (payment_reference),
    KEY payments_customer_lookup_idx (customer_id) INVISIBLE
) ENGINE=InnoDB;

INSERT INTO commerce.payments
SELECT
    n,
    CONCAT('PAY-', LPAD(IF(n > 150000 AND MOD(n, 97) = 0, n - 100000, n), 9, '0')),
    IF(MOD(n, 211) = 0, 600000 + n, 1 + MOD(n * 13, 300000)),
    IF(MOD(n, 101) = 0, 70000 + n, 1 + MOD(n * 19, 50000)),
    IF(MOD(n, 997) = 0, -ROUND(MOD(n, 1000) / 7.0, 2), ROUND(10 + MOD(n, 18000) / 11.0, 2)),
    IF(MOD(n, 499) = 0, 'US_DOLLAR', IF(MOD(n, 11) = 0, 'EUR', 'USD')),
    IF(MOD(n, 601) = 0, 'unknown', IF(MOD(n, 17) = 0, 'failed', 'settled')),
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 400) DAY
FROM operations.numbers
WHERE n <= 200000;

CREATE TABLE operations.audit_events (
    event_id BIGINT UNSIGNED PRIMARY KEY,
    tenant_id INT UNSIGNED NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    actor VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    KEY audit_events_tenant_time_idx (tenant_id, occurred_at)
) ENGINE=InnoDB;

INSERT INTO operations.audit_events
SELECT
    n,
    1 + MOD(n, 80),
    CASE WHEN MOD(n, 50) = 0 THEN 'permission_denied'
         WHEN MOD(n, 17) = 0 THEN 'payment_retry'
         ELSE 'order_updated' END,
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 120) DAY - INTERVAL MOD(n, 86400) SECOND,
    CONCAT('service-', MOD(n, 30)),
    JSON_OBJECT('order_id', 1 + MOD(n, 300000), 'request_id', SHA2(CAST(n AS CHAR), 256))
FROM operations.numbers
WHERE n <= 300000;

CREATE TABLE operations.work_queue (
    job_id BIGINT UNSIGNED PRIMARY KEY,
    queue_name VARCHAR(32) NOT NULL,
    state VARCHAR(20) NOT NULL,
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    available_at DATETIME(6) NOT NULL,
    payload TEXT NOT NULL,
    KEY work_queue_state_available_idx (state, available_at)
) ENGINE=InnoDB;

INSERT INTO operations.work_queue
SELECT
    n,
    IF(MOD(n, 5) = 0, 'billing', 'fulfillment'),
    IF(MOD(n, 9) = 0, 'failed', 'ready'),
    MOD(n, 7),
    UTC_TIMESTAMP(6) - INTERVAL MOD(n, 48) HOUR,
    REPEAT(CONCAT('queue-payload-', n, ';'), 6)
FROM operations.numbers
WHERE n <= 150000;

UPDATE operations.work_queue SET attempts = attempts + 1 WHERE MOD(job_id, 2) = 0;
UPDATE operations.work_queue SET state = 'retry' WHERE MOD(job_id, 3) = 0;
UPDATE operations.work_queue SET payload = CONCAT(payload, 'touched') WHERE MOD(job_id, 5) = 0;
DELETE FROM operations.work_queue WHERE job_id <= 40000;

CREATE TABLE operations.table_maintenance_history (
    history_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    table_schema VARCHAR(64) NOT NULL,
    table_name VARCHAR(64) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    affected_rows BIGINT UNSIGNED NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    note VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

INSERT INTO operations.table_maintenance_history
    (table_schema, table_name, operation, affected_rows, occurred_at, note)
VALUES
    ('operations', 'work_queue', 'DELETE', 40000, UTC_TIMESTAMP(6) - INTERVAL 2 HOUR,
     'retention cleanup; physical reclaim not yet evaluated');

CREATE TABLE operations.feature_flags (
    flag_key VARCHAR(64) PRIMARY KEY,
    enabled BOOLEAN NOT NULL,
    rollout_percent INT UNSIGNED NOT NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;

INSERT INTO operations.feature_flags VALUES
('checkout_v2', TRUE, 25, UTC_TIMESTAMP(6)),
('invoice_async', FALSE, 0, UTC_TIMESTAMP(6)),
('fraud_model_v3', TRUE, 70, UTC_TIMESTAMP(6));

DROP USER IF EXISTS 'vendor_support'@'%';
DROP USER IF EXISTS 'legacy_admin'@'%';
DROP USER IF EXISTS 'readonly_auditor'@'%';
CREATE USER 'vendor_support'@'%' IDENTIFIED BY 'vendor-test-only';
CREATE USER 'legacy_admin'@'%' IDENTIFIED BY 'legacy-test-only';
CREATE USER 'readonly_auditor'@'%' IDENTIFIED BY 'readonly-test-only' REQUIRE SSL;
GRANT PROCESS ON *.* TO 'vendor_support'@'%';
GRANT ALL PRIVILEGES ON commerce.* TO 'vendor_support'@'%';
GRANT PROCESS, RELOAD, CREATE USER ON *.* TO 'legacy_admin'@'%';
GRANT SELECT ON commerce.* TO 'readonly_auditor'@'%';
GRANT SELECT ON operations.* TO 'readonly_auditor'@'%';

ANALYZE TABLE commerce.customers, commerce.orders, commerce.order_items,
    commerce.payments, operations.audit_events, operations.work_queue;
ANALYZE TABLE commerce.customers UPDATE HISTOGRAM ON segment, region WITH 32 BUCKETS;

TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;
DROP TABLE operations.digits;
DROP TABLE operations.numbers;
