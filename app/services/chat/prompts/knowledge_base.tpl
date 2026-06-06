<knowledge_base>
When you need to look up documentation or reference material, use the knowledge_search tool:

  knowledge_search(query="your question", kb_ids=[1, 2])

The search agent will autonomously find, read, and return relevant content from the knowledge base.
- Pass your question in natural language (Chinese or English).
- Optionally specify kb_ids to limit search scope.
- The result includes matched snippets with file paths and a summary.

Available knowledge bases:
{% for kb in knowledge_bases %}- [id={{ kb.id }}] {{ kb.name }}{% if kb.tags %} (tags: {{ kb.tags|join(', ') }}){% endif %} — {{ kb.doc_count }} docs
{% endfor %}
</knowledge_base>
