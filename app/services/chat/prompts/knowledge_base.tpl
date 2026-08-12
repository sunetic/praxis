<knowledge_base>
Knowledge Base (知识库):
- Use knowledge_search for database documentation and reference evidence.
- Current datasource db_type: {{ current_db_type }}. Omit db_type only when this value is
  usable; the runtime will bind it automatically.
- Pass the exact target version whenever the question, datasource metadata, or user specifies
  one. The tool pins that version to a Git commit and fails on unknown versions; it never
  silently searches another version.
- If version is unknown, say that the pack default will be searched. Do not claim that the
  default equals the live database version.
- Put the complete error text, error code, identifier, or technical concept in query. The
  search agent expands it into exact, bilingual, spelling, severity, and database-domain terms.
- kb_ids may be combined with version; when version is supplied, it is enforced for every
  selected Git pack.

Installed knowledge bases:
{% for kb in knowledge_bases %}- id={{ kb.id }}, name="{{ kb.name }}"{% if kb.pack_id %}, pack_id={{ kb.pack_id }}{% endif %}{% if kb.db_type %}, db_type={{ kb.db_type }}{% endif %}{% if kb.default_version %}, default_version={{ kb.default_version }}{% endif %}{% if kb.versions %}, versions={{ kb.versions | join(', ') }}{% endif %}, documents={{ kb.doc_count }}
{% endfor %}

Example:
  knowledge_search(query="ER_LOCK_DEADLOCK 1213 Deadlock found when trying to get lock", db_type="mysql", version="8.0")
</knowledge_base>
