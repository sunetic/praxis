#!/usr/bin/env bash
set -euo pipefail

if [[ ! "${DEMO_EXPORTER_PASSWORD}" =~ ^[a-f0-9]{48}$ ]]; then
  echo "DEMO_EXPORTER_PASSWORD must be a generated 48-character hexadecimal value" >&2
  exit 1
fi

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED BY '${DEMO_EXPORTER_PASSWORD}' WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';

USE app;
CREATE TABLE IF NOT EXISTS orders (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    customer_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_orders_customer (customer_id),
    KEY idx_orders_status_created (status, created_at)
);

INSERT INTO orders (customer_id, status)
SELECT seq, IF(seq % 5 = 0, 'pending', 'complete')
FROM (
    SELECT ones.n + tens.n * 10 + 1 AS seq
    FROM
      (SELECT 0 n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
       UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) ones
    CROSS JOIN
      (SELECT 0 n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
       UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) tens
) seed
WHERE NOT EXISTS (SELECT 1 FROM orders LIMIT 1);
SQL
