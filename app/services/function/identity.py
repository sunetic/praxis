from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable

FUNCTION_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9_-]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def compact_whitespace(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_function_display_name(raw_name: object) -> str:
    return compact_whitespace(raw_name)


def validate_function_display_name(name: str) -> str | None:
    if not name:
        return "name is required"
    if len(name) > 255:
        return "name must be at most 255 characters"
    return None


def normalize_function_slug(raw_slug: object) -> str:
    text = compact_whitespace(raw_slug).lower()
    text = re.sub(r"\s+", "-", text)
    text = _NON_SLUG_CHARS_RE.sub("-", text)
    text = _MULTI_DASH_RE.sub("-", text).strip("-_")
    if text and not text[0].isalpha():
        text = f"fn-{text}"
    text = text[:64].strip("-_")
    return text


def validate_function_slug(slug: str) -> bool:
    return bool(FUNCTION_SLUG_RE.fullmatch(slug))


def build_function_slug_base(display_name: str) -> str:
    normalized_name = normalize_function_display_name(display_name)
    ascii_name = (
        unicodedata.normalize("NFKD", normalized_name).encode("ascii", "ignore").decode("ascii")
    )
    slug = normalize_function_slug(ascii_name)
    if not validate_function_slug(slug):
        digest = hashlib.sha1(normalized_name.encode("utf-8")).hexdigest()[:8]
        slug = f"fn-{digest}"
    if not validate_function_slug(slug):
        slug = f"fn-{hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8]}"
    return slug[:64]


def generate_unique_function_slug(
    display_name: str,
    *,
    exists: Callable[[str], bool],
) -> str:
    base = build_function_slug_base(display_name)
    if not exists(base):
        return base

    suffix = 2
    while True:
        suffix_text = f"-{suffix}"
        head = base[: max(1, 64 - len(suffix_text))].rstrip("-_")
        candidate = f"{head}{suffix_text}"
        if validate_function_slug(candidate) and not exists(candidate):
            return candidate
        suffix += 1
