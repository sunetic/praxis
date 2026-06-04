from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template

_SERVICES_ROOT = Path(__file__).parent.parent

_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_SERVICES_ROOT)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
    return _env


class PromptLoader:
    """Load and render Jinja2 prompt templates from app/services/.

    Templates are cached after first load. Uses StrictUndefined so missing
    variables raise UndefinedError immediately rather than silently rendering
    as empty strings.

    Usage:
        PromptLoader.render("chat/prompts/scene_agents/sql_analysis.tpl",
                            key="sql_analysis", context_json="{}", ...)
    """

    _cache: dict[str, Template] = {}

    @classmethod
    def load(cls, rel_path: str) -> Template:
        """Load template by path relative to app/services/."""
        if rel_path not in cls._cache:
            cls._cache[rel_path] = _get_env().get_template(rel_path)
        return cls._cache[rel_path]

    @classmethod
    def render(cls, rel_path: str, **kwargs: Any) -> str:
        """Render template with named parameters."""
        return cls.load(rel_path).render(**kwargs)
