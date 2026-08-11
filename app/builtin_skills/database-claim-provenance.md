---
name: database-claim-provenance
version: 1.0.0
description: Database analysis claim provenance — audit metrics, classifications, ratings, absence claims, and causal conclusions against query evidence before reporting
database: general
always_apply: false
source: built_in
---
# Database Claim Provenance

## Goal

Produce database analysis whose material claims can be independently traced to source data, query logic, explicit user input, or a clearly labelled assumption.

## Analysis rules

1. For each reported metric, retain the query and explain non-obvious formulas, population, grain, filters, and exclusions.
2. Treat SQL as evidence only when it derives the claim from source rows. A literal value, output alias, intent string, or authored label does not prove the claim it names.
3. Reconcile headline totals with displayed breakdowns. If a headline includes unmapped or orphan records while a named-entity breakdown excludes them, explain the difference where those numbers are presented. Check join fan-out, deduplication, null handling, numerator/denominator compatibility, and sampled or truncated results.
4. Treat classifications, scores, severity levels, rankings, and causal diagnoses as derived claims. Require a source field or an explicit rubric with evidenced inputs.
5. If the user authorizes a heuristic based on names, keywords, or free text, label it as heuristic and show the rule. Otherwise, do not present it as a source-backed dimension.
6. Treat absence as a factual claim. Inspect the relevant schema or data before saying that a field, relationship, population, or dimension is missing; uninspected means unknown.
7. In the final answer, distinguish directly observed facts, derived conclusions, assumptions, and unavailable information.

<completion_verification_policy>
For database analysis, verify every material claim against the actual query request and returned evidence, not its alias or narrative intent. Numeric claims must use compatible populations, units, grains, filters, and inclusion rules; reconcile totals with displayed components and reject conclusions based on sampled or truncated rows when a complete aggregate is required. When a headline population includes unmapped or orphan records but an entity breakdown excludes them, require an explicit reconciliation at the point where those values are presented. Audit joins, fan-out, deduplication, null handling, and authored constants for circular evidence. Classifications, ratings, rankings, severity levels, and causal conclusions require a source field or an explicit rubric mapped to evidenced inputs. Name-, keyword-, or free-text heuristics are allowed only when the user requested them and the answer labels both the rule and result as heuristic. Absence claims require direct inspection of the relevant schema or data; uninspected means unknown.
</completion_verification_policy>
