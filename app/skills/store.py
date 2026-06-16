import os
import re
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import fmt_kv, get_logger

logger = get_logger("skills.store")


VALID_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
VALID_DATABASE = {"mysql", "postgresql", "general"}
VALID_SOURCE = {"built_in", "custom"}
CUSTOM_SKILLS_DIR = "custom"
REFERENCE_SEPARATOR = "<!-- skill:reference -->"


@dataclass
class Skill:
    name: str
    version: str
    description: str
    database: str
    always_apply: bool
    prompt: str
    source: str = "custom"
    path: str = ""
    rules_prompt: str = ""
    reference_prompt: str = ""

    def to_dict(self, include_prompt: bool = True):
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "database": self.database,
            "always_apply": self.always_apply,
            "prompt": self.prompt if include_prompt else "",
            "source": self.source,
            "path": self.path,
        }


class SkillValidationError(ValueError):
    pass


def _split_rules_reference(prompt: str) -> tuple[str, str]:
    if REFERENCE_SEPARATOR in prompt:
        parts = prompt.split(REFERENCE_SEPARATOR, 1)
        return parts[0].strip(), parts[1].strip()
    return prompt, ""


def _validate_skill_fields(
    name: str,
    version: str,
    description: str,
    database: str,
    always_apply: bool,
    prompt: str,
    source: str,
) -> None:
    normalized_name = name.strip()
    if len(normalized_name) < 2 or len(normalized_name) > 64:
        raise SkillValidationError("Skill name length must be 2-64 characters")
    if any(ch in normalized_name for ch in ["/", "\\", "\n", "\r", "\t"]):
        raise SkillValidationError("Skill name contains invalid path/control characters")
    if not VALID_VERSION_PATTERN.match(version):
        raise SkillValidationError("Skill version must use semantic version format x.y.z")
    if len(description.strip()) < 8:
        raise SkillValidationError("Skill description must be at least 8 characters")
    if database not in VALID_DATABASE:
        raise SkillValidationError("Skill database must be one of: general, mysql, postgresql")
    if not isinstance(always_apply, bool):
        raise SkillValidationError("Skill always_apply must be boolean")
    if not prompt.strip():
        raise SkillValidationError("Skill prompt cannot be empty")
    if source not in VALID_SOURCE:
        raise SkillValidationError("Skill source must be one of: built_in, custom")


def _render_skill_file(skill: Skill) -> str:
    front_matter = yaml.safe_dump(
        {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "database": skill.database,
            "always_apply": skill.always_apply,
            "source": skill.source,
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{front_matter}\n---\n{skill.prompt.strip()}\n"


class SkillStore:
    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = os.path.abspath(skills_dir)
        self.skills: dict[str, Skill] = {}
        self.errors: list[dict[str, str]] = []

    def _ensure_skills_dir(self) -> None:
        os.makedirs(self.skills_dir, exist_ok=True)

    def _parse_skill_file(self, path: str) -> Skill:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            raise SkillValidationError(f"Read skill file failed: {str(e)}") from e

        match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
        if not match:
            raise SkillValidationError("Skill file must contain YAML front matter")

        front_matter, prompt = match.groups()
        metadata = yaml.safe_load(front_matter)
        if not isinstance(metadata, dict):
            raise SkillValidationError("Skill front matter must be a valid YAML object")

        name = str(metadata.get("name") or "").strip()
        version = str(metadata.get("version") or "1.0.0").strip()
        description = str(metadata.get("description") or "").strip()
        database = str(metadata.get("database") or "").strip()
        always_apply = metadata.get("always_apply")
        source = self._resolve_skill_source(path, metadata.get("source"))
        prompt_text = prompt.strip()
        _validate_skill_fields(name, version, description, database, always_apply, prompt_text, source)
        rules, reference = _split_rules_reference(prompt_text)
        return Skill(
            name=name,
            version=version,
            description=description,
            database=database,
            always_apply=always_apply,
            prompt=prompt_text,
            source=source,
            path=path,
            rules_prompt=rules,
            reference_prompt=reference,
        )

    def _resolve_skill_source(self, path: str, source_value: Any) -> str:
        if isinstance(source_value, str) and source_value.strip():
            normalized = source_value.strip().lower().replace("-", "_")
            if normalized in {"builtin", "built_in"}:
                return "built_in"
            if normalized == "custom":
                return "custom"
            raise SkillValidationError("Skill source must be one of: built_in, custom")

        try:
            rel = Path(path).resolve().relative_to(Path(self.skills_dir).resolve())
            parts = rel.parts
        except ValueError:
            parts = ()

        if parts and parts[0].lower() == CUSTOM_SKILLS_DIR:
            return "custom"
        return "built_in"

    def _path_for_skill_name(self, name: str, database: str, source: str = "custom") -> str:
        if source != "custom":
            raise SkillValidationError("Only custom skills can be written by API")
        encoded_name = quote(name, safe="")
        return str(Path(self.skills_dir) / CUSTOM_SKILLS_DIR / database / f"{encoded_name}.md")

    def _write_skill_file(self, path: str, skill: Skill) -> None:
        content = _render_skill_file(skill)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _ensure_custom_skill(self, skill: Skill) -> None:
        if skill.source != "custom":
            raise SkillValidationError(f"Built-in skill '{skill.name}' is read-only")

    def load(self) -> list[Skill]:
        self.skills = {}
        self.errors = []
        self._ensure_skills_dir()

        loaded: list[Skill] = []
        scanned_count = 0
        for root, _, files in os.walk(self.skills_dir):
            for file in files:
                if not file.endswith(".md"):
                    continue
                if file.lower() == "readme.md":
                    continue
                scanned_count += 1
                path = os.path.join(root, file)
                try:
                    skill = self._parse_skill_file(path)
                except SkillValidationError as e:
                    error = {"path": path, "error": str(e)}
                    self.errors.append(error)
                    logger.warning(
                        "skill_parse_failed %s error=%s",
                        fmt_kv(path=path),
                        str(e),
                    )
                    continue
                if skill.name in self.skills:
                    conflict = self.skills[skill.name]
                    logger.warning(
                        "skill_name_conflict %s",
                        fmt_kv(name=skill.name, kept=conflict.path, ignored=path),
                    )
                    self.errors.append(
                        {
                            "path": path,
                            "error": f"Duplicate skill name '{skill.name}', ignored",
                        }
                    )
                    continue
                self.skills[skill.name] = skill
                loaded.append(skill)

        logger.info(
            "skill_load_done %s",
            fmt_kv(
                skills_dir=self.skills_dir,
                scanned=scanned_count,
                loaded=len(loaded),
                errors=len(self.errors),
            ),
        )
        return loaded

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def list_skills(self) -> list[Skill]:
        return sorted(self.skills.values(), key=lambda item: item.name.lower())

    def search(self, query: str | None = None) -> list[Skill]:
        items = self.list_skills()
        if not query:
            return items
        q = query.strip().lower()
        if not q:
            return items
        return [s for s in items if q in s.name.lower() or q in s.description.lower()]

    def create(
        self,
        name: str,
        version: str,
        description: str,
        database: str,
        always_apply: bool,
        prompt: str,
    ) -> Skill:
        self.load()
        if name in self.skills:
            raise SkillValidationError(f"Skill '{name}' already exists")
        source = "custom"
        _validate_skill_fields(name, version, description, database, always_apply, prompt, source)
        path = self._path_for_skill_name(name, database, source=source)
        skill = Skill(
            name=name,
            version=version,
            description=description,
            database=database,
            always_apply=always_apply,
            prompt=prompt,
            source=source,
            path=path,
        )
        self._write_skill_file(path, skill)
        self.load()
        return self.skills[name]

    def update(
        self,
        original_name: str,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        database: str | None = None,
        always_apply: bool | None = None,
        prompt: str | None = None,
    ) -> Skill:
        self.load()
        current = self.skills.get(original_name)
        if not current:
            raise SkillValidationError(f"Skill '{original_name}' not found")
        self._ensure_custom_skill(current)

        next_skill = Skill(
            name=name or current.name,
            version=version or current.version,
            description=description or current.description,
            database=database or current.database,
            always_apply=current.always_apply if always_apply is None else always_apply,
            prompt=prompt if prompt is not None else current.prompt,
            source=current.source,
            path=current.path,
        )
        _validate_skill_fields(
            next_skill.name,
            next_skill.version,
            next_skill.description,
            next_skill.database,
            next_skill.always_apply,
            next_skill.prompt,
            next_skill.source,
        )
        if next_skill.name != original_name and next_skill.name in self.skills:
            raise SkillValidationError(f"Skill '{next_skill.name}' already exists")

        target_path = self._path_for_skill_name(
            next_skill.name,
            next_skill.database,
            source=next_skill.source,
        )
        next_path = target_path if target_path != current.path else current.path
        self._write_skill_file(next_path, next_skill)
        if next_path != current.path and os.path.exists(current.path):
            os.remove(current.path)
        self.load()
        updated = self.skills.get(next_skill.name)
        if not updated:
            raise SkillValidationError("Skill update failed due to parser validation")
        return updated

    def delete(self, name: str) -> None:
        self.load()
        target = self.skills.get(name)
        if not target:
            raise SkillValidationError(f"Skill '{name}' not found")
        self._ensure_custom_skill(target)
        if os.path.exists(target.path):
            os.remove(target.path)
        self.load()


skill_store = SkillStore()
