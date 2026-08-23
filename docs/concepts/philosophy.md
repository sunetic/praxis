# Positioning and Design Philosophy

## What Praxis is designed to solve

Database engineers understand data, SQL, and runtime behavior, but turning one investigation into a reusable team tool often also requires API, UI, scheduling, and deployment work. Praxis shortens that distance: **people describe the goal and make critical decisions, AI understands and plans the work, and the platform executes safely while preserving evidence.**

It is neither a database Q&A chatbot nor an automation script that lets a model operate production freely. Praxis is a database AI workspace: exploration starts in conversation and can mature into an Agent, Skill, Function, or Scheduler.

## Four design principles

### AI understands intent; the platform constrains execution

Natural language is ambiguous, and complex tasks often need clarification, so intent understanding and planning belong to the model. Database permissions, change switches, high-risk confirmations, and object lifecycle rules cannot depend on the model remembering them; the platform enforces those constraints on the execution path.

### Results need evidence

Good database advice must do more than sound plausible. Praxis encourages Agents to inspect schemas, runtime state, execution plans, or knowledge-base material before drawing conclusions. When evidence is incomplete, the Agent should state the uncertainty and the next verification step.

### Exploration can become reusable capability

A conversation is only the starting point. Effective professional methods can become Skills; stable combinations of roles, tools, and knowledge can become Agents; more deterministic processes can become Functions and run periodically through Scheduler.

### People stay in the critical decision loop

Queries, diagnosis, and recommendations can be highly automated, but database writes, session termination, object deletion, and similar actions need stricter authorization and confirmation. Safe defaults should be conservative, and users should be able to see what the system plans to do and what it actually did.

## Praxis's focus

Praxis optimizes for long-running database work instead of trying to be a general agent that does everything:

- Datasources, database types, and connection scope are first-class concepts.
- Tools read and diagnose data; platform capabilities manage Praxis objects.
- Long contexts may be compacted, but critical constraints, recent conversation, and execution evidence must be retained.
- Reliably completing real work matters more than the prose style of a single response.
- Models are replaceable, and model selection should be supported by the same real-task Eval suite.

Continue with [Core Concepts](mental-model.md) for an overview of how the product modules fit together.
