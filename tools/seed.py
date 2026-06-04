"""Seed script — registers builtin resources if available (EE-only)."""

from app.db.database import SessionLocal

with SessionLocal() as db:
    try:
        from app.builtin_functions import register_builtin_functions
        print("functions:", register_builtin_functions(db))
    except ImportError:
        print("functions: skipped (CE)")

    try:
        from app.builtin_agents import register_builtin_agents
        print("agents:", register_builtin_agents(db))
    except ImportError:
        print("agents: skipped (CE)")

    try:
        from app.builtin_knowledge import register_builtin_knowledge
        print("knowledge:", register_builtin_knowledge(db))
    except ImportError:
        print("knowledge: skipped (CE)")
