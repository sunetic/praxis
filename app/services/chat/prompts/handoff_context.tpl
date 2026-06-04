<handoff_context>
Handoff Context (first turn only):
- source: {{ source_label }} / {{ source_entry }}
- type: {{ handoff_type }}
{% if title %}- title: {{ title }}
{% endif %}{% if summary %}- summary: {{ summary }}
{% endif %}{% if datasource_line %}- datasource: {{ datasource_line }}
{% endif %}{% if focus_line %}- focus: {{ focus_line }}
{% endif %}{% if facts %}- key_facts:
{% for item in facts %}  - {{ item.label }}: {{ item.value }}
{% endfor %}{% endif %}{% if sql_text %}SQL Text:
{{ sql_text }}
{% endif %}{% if current_plan_line %}- current_plan: {{ current_plan_line }}
{% endif %}{% if signals %}- signals:
{% for item in signals %}  - {{ item }}
{% endfor %}{% endif %}{% if ai_summary %}- ai_summary: {{ ai_summary }}
{% endif %}{% if investigation_steps %}- suggested_investigation:
{% for item in investigation_steps %}  - {{ item }}
{% endfor %}{% endif %}- Treat these as page-provided facts for the first turn. Continue naturally without asking the user to restate them.
- For this first handoff turn, answer directly from these facts/signals/ai_summary. Do not call tools in this turn.
- If the current evidence is still insufficient, say what should be verified next instead of launching a broad investigation.
</handoff_context>
