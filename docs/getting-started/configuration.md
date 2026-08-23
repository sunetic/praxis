# Configuration and Security

Configure the model during first-run setup, on the **Settings** page, or through environment variables. Settings saved in the UI suit a single self-hosted instance; automated deployments usually benefit from explicit environment and persistent-volume management.

## Model configuration

Praxis uses an OpenAI-compatible Chat Completions API. At minimum, configure:

| Setting | Description |
| --- | --- |
| `AI_BASE_URL` | The compatible service's `/v1` base URL |
| `AI_API_KEY` | Model-service credential |
| `AI_MODEL` | A model name actually supported by the service |

The model needs reliable multi-turn conversation and tool calling. Passing ordinary chat does not prove it can complete Praxis database tasks reliably; run the [Eval suite](../reliability/evaluation.md) before switching models.

## Data and secrets

- Persist `/app/data` in Docker deployments; it contains Praxis management data.
- Datasource passwords are encrypted at rest. Production deployments must set a stable, strongly random `SECRET_KEY` or `DATASOURCE_ENCRYPTION_KEY`.
- Never put API keys, database passwords, or real production data in the repository, Eval cases, or reports.
- Prefer native read-only database accounts and restrict network sources and accessible schemas at the database layer.

## Write-operation switch

SQL writes are disabled by default under **Settings → Security**. Enabling the switch only allows the relevant execution paths to continue evaluating the action; it does not approve every change. High-risk actions such as session termination or object deletion may still require explicit confirmation.

## Long contexts

Under **Settings → LLM**, configure the model context window and automatic compaction threshold. The window must match the provider's actual model limit. A threshold that is too high may not compact before the provider rejects a request; one that is too low discards detail prematurely. Start with the defaults, then validate adjustments with long tasks and the [Eval suite](../reliability/evaluation.md).

See `.env.example` in the repository root for the complete environment-variable template.
