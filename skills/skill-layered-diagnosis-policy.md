---
name: skill-layered-diagnosis-policy
version: 1.0.0
description: Global layered diagnosis policy — routine first, then deep-dive with user confirmation
database: general
always_apply: true
---
# Global Layered Diagnosis Policy (always-on)

Applies to all diagnosis-related skills.

## Execution Rules

1. Start with pre-checks and routine diagnostics; deliver actionable conclusions first.
2. Before deep analysis, ask the user whether to continue.
3. After completing each layer, summarize findings and confirm before going deeper.
4. Do not enter the next layer without user confirmation.

## Conversation Rules

- Do not expose internal tier labels (e.g., L0/L1/L2/L3/L4) to the user.
- Use natural language to ask, e.g., "Would you like me to analyze further?"

## Cost Rules

- Prefer low-cost, high-information-density actions.
- Before deep analysis, explain the expected benefit and additional cost.
- Do not execute a dependent action in the same round as its prerequisite; collect evidence first, then decide the next round.

## Priority with API-oriented Skills

- If an active skill defines an API-first workflow, that skill's workflow takes priority over this policy's default "routine diagnostics" path.
- Do not skip preflight / knowledge lookup / API call steps required by an active skill under the pretext of "routine diagnostics."
