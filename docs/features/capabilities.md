# Capabilities and Safety

The **Capabilities** page lists the structured actions currently available to the model and platform. It answers two questions: what can an Agent do, and what parameters and risk boundaries apply to an action?

## Two kinds of capability

- **LLM Tools** provide data-plane operations such as reading schemas, running queries, obtaining execution plans, and searching knowledge.
- **Platform capabilities** create, modify, publish, or run Praxis objects.

The distinction matters. Database queries are governed by database permissions and SQL safety policy; platform-object changes are governed by object constraints, confirmation, and audit. The model selects and fills an action, but the server still validates its parameters and permissions.

## Inspecting a capability

Capability details show its description and parameter schema. Before using one, check:

- Required parameters and target objects.
- Whether it can change database or platform state.
- Whether human confirmation is required.
- Whether repeated execution is safe.
- Whether failures can be diagnosed through run history or tracing.

The capability list in a running instance is the source of truth. This documentation explains product semantics; it does not promise that every deployment, version, or account exposes the same capabilities.
