SELECT status, count(*), sum(total_amount)
FROM commerce.orders
WHERE status IN ('refunded', 'pending', 'cancelled', 'shipped')
  AND created_at >= now() - interval '30 days'
GROUP BY status;

SELECT c.region, c.segment, count(*), sum(o.total_amount)
FROM commerce.customers c
JOIN commerce.orders o ON o.customer_id = c.customer_id
WHERE c.segment = 'enterprise' AND c.region = 'APAC'
GROUP BY c.region, c.segment;

SELECT count(*)
FROM operations.audit_events
WHERE payload @> '{"order_id": 4242}'::jsonb;
