<db_type_guidance>
Database Type: MySQL
- Slow SQL analysis: use EXPLAIN / EXPLAIN ANALYZE to view execution plans; query performance_schema.events_statements_summary_by_digest for historical statistics.
- Process inspection: SHOW PROCESSLIST or SELECT * FROM information_schema.PROCESSLIST.
- Connection count: SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections'.
- Lock analysis: SELECT * FROM performance_schema.data_locks; SHOW ENGINE INNODB STATUS.
- Table structure: INFORMATION_SCHEMA.TABLES, INFORMATION_SCHEMA.COLUMNS, SHOW CREATE TABLE.
- Index optimization: in EXPLAIN output, focus on type (ALL = full table scan), key, rows, and Extra columns; SHOW INDEX FROM <table>.
- Storage engine: SHOW TABLE STATUS to check the Engine column; InnoDB is the default.
- Variables and status: SHOW VARIABLES / SHOW STATUS to view runtime parameters and statistics.
- Syntax notes: supports SHOW TABLES / SHOW DATABASES / DESCRIBE; use backticks to quote reserved words.
</db_type_guidance>
