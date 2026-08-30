# MySQL DBA Eval

This directory contains the executable MySQL DBA Eval suite, fixture, knowledge material, and scoring rules. See the [Evaluation documentation](../../docs/reliability/evaluation.md) for shared commands, options, assessment rules, and reporting guidance. See [MySQL DBA Cases](../../docs/reliability/mysql-dba-eval.md) for the case catalog.

The fixture uses `mysql:8.4`. The default `praxis` profile exercises the real backend and HTTP/SSE Chat path; the `model` profile uses the fixed model-comparison harness. All accounts, data, and policies are synthetic and isolated from production environments.
