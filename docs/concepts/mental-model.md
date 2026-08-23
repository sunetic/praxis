# Core Concepts

Praxis can be understood as four layers.

| Layer | Main objects | Purpose |
| --- | --- | --- |
| Connections | Datasources, services, knowledge bases, channels | Connect databases, external control planes, domain material, and notification destinations |
| Intelligence | Chat, Agent, Skill | Understand tasks, call tools, and reuse roles and methods |
| Automation | Function, Scheduler | Turn stable workflows into testable, publishable, and scheduled capabilities |
| Governance | Capabilities, security settings, run history, tracing, Eval | Control boundaries and measure reliability |

## Common workflows

### From a question to a reusable Agent

1. Select a datasource in Chat and describe the goal.
2. The Agent reads database evidence and, when needed, uses knowledge bases and specialist Skills.
3. Refine the scope, decision criteria, and output through conversation until the workflow is stable.
4. Save it as an Agent, then run it against other datasources or schedule it with Scheduler.

### From exploration to deterministic automation

1. Explore a workable process in Chat.
2. When inputs, outputs, and execution logic are clear, build a Function.
3. Test it in the build workspace with representative inputs and verify the business result before publishing.
4. Run it directly or trigger it periodically with Scheduler.

## Choosing Agent, Skill, or Function

| Requirement | Best fit |
| --- | --- |
| The model must decide the next step from live evidence | Agent |
| An Agent needs domain methods, rules, or references | Skill |
| Inputs and outputs are stable and repeatability matters | Function |
| An Agent or Function must run on a schedule | Scheduler |

These objects are composable. An Agent can use multiple Skills and tools, and a Scheduler can run an Agent or Function.
