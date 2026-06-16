<knowledge_base>
When you need to look up database documentation or reference material, use the knowledge_search tool:

  knowledge_search(query="your question", db_type="mysql", version="8.0")

- Pass db_type matching the current datasource type (e.g. mysql, postgresql).
- Pass version if you know the target database version. If omitted, uses the currently installed version.
- The search agent will automatically find the right knowledge pack and return relevant content with snippets and a summary.
- You can also pass kb_ids to search specific knowledge bases by ID.

Installed knowledge bases: {{ knowledge_bases | length }}
</knowledge_base>
