<knowledge_base>
Knowledge Base:
When you need to look up documentation, you can access the platform knowledge base:
1. Use object_crud(object_type="knowledge_base", action="list") to list all knowledge bases.
2. Use object_crud(object_type="knowledge_document", action="list", payload={"kb_id": N}) to get the document list and file paths.
3. First use exec_command(command="ls", args=["data/knowledge/N/"]) to view the directory structure; narrow down to the relevant subdirectory before searching content.
4. Break the user's question into several categories of search terms, then search: target object / action verb / metric or attribute / API domain term / abbreviation and full name.
  - For each category, dynamically generate English technical keywords, synonyms, related action words, and object names — do not reuse fixed keyword templates, and do not search with only a single term.
  - First form 2-3 keyword combinations, then preferably use exec_command with rg for multi-keyword search, using alternation or multiple -e flags with dynamically generated keywords.
  - Alternation example: exec_command(command="rg", args=["-n", "-i", "-e", "kw1|kw2|kw3", "data/knowledge/1/target-dir/"])
  - Multi-pattern example: exec_command(command="rg", args=["-n", "-i", "-e", "kw1", "-e", "kw2", "-e", "kw3", "data/knowledge/1/"])
  - After a match, use exec_command(command="sed", args=["-n", "20,80p", "data/knowledge/1/some.md"]) or cat to read the surrounding context in detail.
  - grep can still serve as a compatible fallback, but prefer rg for complex searches; sed is only for reading snippets, not for modifying files.
5. After matching a document, you must read its content and confirm the API path, method, and query/body parameter format before proceeding.

Available knowledge bases:
{% for kb in knowledge_bases %}- [id={{ kb.id }}] {{ kb.name }}{% if kb.tags %} (tags: {{ kb.tags|join(', ') }}){% endif %} — {{ kb.doc_count }} docs — data/knowledge/{{ kb.id }}/
{% endfor %}
</knowledge_base>
