# Skills

A Skill gives an Agent domain methods and rules. It describes, in reviewable text, what to inspect for a class of problem, how to make decisions, and which limits apply. It does not store database credentials or represent a single run.

## What belongs in a Skill

- Diagnostic steps for slow SQL, lock waits, replication, Vacuum, and similar work.
- Team evidence checklists and output formats.
- Database-specific considerations.
- Security or compliance rules that must always be followed.

A Skill can target general use, MySQL, PostgreSQL, or OceanBase. With `always apply` enabled, it is included in relevant conversations by default. Other Skills are selected in an Agent configuration or loaded as needed for a task.

## Writing guidance

A useful Skill states when it applies, the investigation order, required evidence, prohibited actions, decision thresholds, and completion criteria. Do not include hostnames, accounts, or secrets from a specific incident, and do not turn it into background prose without verification steps.

Built-in Skills provide ready-to-use methods and are protected in the interface. Custom Skills can be created and maintained from the list or guided builder.
