# Services

A service registers an external control plane and associates it with a cluster or datasource. It differs from a datasource: a datasource connects directly to a database, while a service connects to a management API.

The current interface supports OCP API services. Enter the address, port, and authentication information, then test the connection before saving or from the service list. Associate the service with either a cluster identifier or an existing datasource.

## Guidance

- Confirm that the target management API is reachable from the Praxis host.
- Use a dedicated, least-privilege service account.
- Test authentication and address resolution; a saved configuration alone does not prove connectivity.
- Associate each service with a clear resource scope instead of using one credential for unrelated clusters.
- Before deletion, check whether any Agent or automation depends on it.

Available operations depend on the service type and account permissions. Successful configuration only proves that the API is reachable; it does not authorize every management action.
