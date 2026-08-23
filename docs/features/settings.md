# Settings

The Settings page centralizes model, builder-engine, and security configuration.

## LLM

Enter an API key, model name, and OpenAI-compatible base URL, then configure the context window and automatic compaction threshold. The model name must be supported by the service.

After changing models, verify a normal Chat and tool call. For long-running or important work, run the [Eval suite](../reliability/evaluation.md) to compare reliability, evidence use, and provider availability.

## Builder engine

Function building can use the built-in engine or a configured external CLI. Test the connection before using an external CLI; the platform may add flags required for non-interactive execution. Successful configuration does not mean generated results have passed business acceptance.

## Security

SQL writes are disabled by default. Enable them only when there is a clear need, datasource privileges are minimized, and the workflow includes confirmation and rollback. Platform settings work together with database permissions; they do not replace database access control.

Never include API keys or other sensitive fields in screenshots, issues, Eval reports, or commits.
