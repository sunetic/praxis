<db_type_guidance>
Database Type: PostgreSQL
- Slow SQL analysis: use EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) to view execution plans; use the pg_stat_statements extension for historical statistics.
- Process inspection: SELECT * FROM pg_stat_activity.
- Connection count: SELECT count(*) FROM pg_stat_activity; SHOW max_connections.
- Lock analysis: join pg_locks with pg_stat_activity; use the pg_blocking_pids() function.
- Table structure: information_schema.tables, information_schema.columns, pg_catalog.pg_class.
- Index optimization: check pg_stat_user_indexes idx_scan to evaluate index usage; check pg_stat_user_tables seq_scan for full table scan counts.
- Sequences / auto-increment: use SEQUENCE and SERIAL/BIGSERIAL types, not AUTO_INCREMENT.
- Tablespace and size: pg_size_pretty(pg_total_relation_size('table')) to view table size.
- Syntax notes: SHOW TABLES is not supported (use \dt or information_schema); use single quotes for strings; use double quotes for identifiers; use ::type syntax for type casting.
</db_type_guidance>
