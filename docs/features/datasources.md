# Datasources

A datasource defines which database Praxis can access. It is the foundation for Chat, Function, Scheduler, and other workflows that use real data.

## Currently supported

- MySQL.
- PostgreSQL.

The connection form selects common default ports by database type and also accepts custom hosts, ports, users, and default databases.

## Recommended onboarding flow

1. Create a dedicated least-privilege account in the database.
2. Add the datasource and enter its connection details.
3. Test the connection before saving.
4. Use a read-only Chat task to verify that the expected schemas are readable and out-of-scope data is not.
5. Then make the datasource available to Agents, Functions, or Schedulers.

## Security and operations

- Prefer read-only accounts for production diagnosis; do not reuse administrator accounts.
- Connectivity is determined from the Praxis host. A database being reachable from the browser does not mean it is reachable from the container.
- Changing the encryption key makes saved passwords impossible to decrypt.
- Before deleting a datasource, check the Schedulers and Functions that depend on it.

For separate tenants or permission boundaries in the same cluster, create separate datasources so the scope remains explicit in run records.
