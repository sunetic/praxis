---
name: database-claim-provenance
version: 1.1.0
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
