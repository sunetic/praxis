SELECT status, COUNT(*) AS order_count, SUM(total_amount) AS revenue
FROM commerce.orders
WHERE created_at >= UTC_TIMESTAMP() - INTERVAL 30 DAY
  AND status IN ('refunded', 'pending', 'cancelled', 'shipped')
GROUP BY status;

SELECT c.segment, c.region, COUNT(*) AS order_count, SUM(o.total_amount) AS revenue
FROM commerce.customers c
JOIN commerce.orders o ON o.customer_id = c.customer_id
WHERE c.segment = 'enterprise' AND c.region = 'APAC'
GROUP BY c.segment, c.region;

SELECT payment_reference, COUNT(*) AS duplicate_count
FROM commerce.payments
GROUP BY payment_reference
HAVING COUNT(*) > 1
LIMIT 25;

SELECT state, COUNT(*) AS jobs
FROM operations.work_queue
WHERE available_at <= UTC_TIMESTAMP()
GROUP BY state;
