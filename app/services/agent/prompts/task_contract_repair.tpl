The first task-contract interpretation classified this as a complex task but returned no independently
verifiable acceptance criteria. Re-read the user's request semantically and return a complete replacement
contract using the same JSON schema.

Do not invent requirements. Each criterion must be grounded by a short exact source_excerpt from the user.
If the request asks for several observable findings, decisions, actions, or report sections, represent those
outcomes as acceptance criteria. Keep answer-text qualities and tool-backed external facts distinct through
requires_tool_evidence. Return exactly one JSON object and no prose.

FIRST INTERPRETATION:
{{ first_contract_json }}
