from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import DEFAULT_DATA_DIR
from app.core.logging import fmt_kv, get_logger

logger = get_logger("knowledge.search_tools")

_DATA_ROOT = DEFAULT_DATA_DIR / "knowledge"
_RG_TIMEOUT = 15
_MAX_OUTPUT_BYTES = 30_000
_MAX_READ_LINES = 200
_KB_META_FILE = ".kb_meta.json"


def _resolve_kb_root(kb_id: int) -> Path:
    base = _DATA_ROOT / str(kb_id)
    if not base.is_dir():
        raise FileNotFoundError(f"Knowledge base directory not found: {base}")
    meta = read_kb_meta(kb_id)
    if meta:
        local_path = meta.get("local_path")
        if local_path:
            lp = Path(local_path)
            if lp.is_dir():
                return lp.resolve()
        subdir = meta.get("subdirectory", "")
        if subdir:
            root = base / subdir
            if root.is_dir():
                return root.resolve()
    return base.resolve()


def read_kb_meta(kb_id: int) -> dict[str, Any] | None:
    meta_file = _DATA_ROOT / str(kb_id) / _KB_META_FILE
    if not meta_file.is_file():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def find_kb_by_db_type(db_type: str) -> int | None:
    if not _DATA_ROOT.is_dir():
        return None
    for entry in _DATA_ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            kb_id = int(entry.name)
        except ValueError:
            continue
        meta = read_kb_meta(kb_id)
        if meta and meta.get("db_type") == db_type:
            return kb_id
    return None


def _extract_frontmatter_title(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            in_frontmatter = False
            for line in f:
                stripped = line.strip()
                if stripped == "---" and not in_frontmatter:
                    in_frontmatter = True
                    continue
                if stripped == "---" and in_frontmatter:
                    break
                if in_frontmatter and stripped.lower().startswith("title:"):
                    return stripped.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


# ── kb_discover ──────────────────────────────────────────────────────────

def discover(kb_id: int, query: str, max_results: int = 20) -> list[dict[str, Any]]:
    root = _resolve_kb_root(kb_id)
    keywords = [w.lower() for w in re.split(r"[\s,|]+", query) if w.strip()]
    if not keywords:
        return []

    results: list[dict[str, Any]] = []
    for md_path in sorted(root.rglob("*.md")):
        if md_path.name.lower() == "readme.md":
            continue
        rel = str(md_path.relative_to(root))
        title = _extract_frontmatter_title(md_path)
        searchable = f"{rel} {title}".lower()
        score = sum(1 for kw in keywords if kw in searchable)
        if score > 0:
            results.append({
                "path": rel,
                "title": title or md_path.stem,
                "size_bytes": md_path.stat().st_size,
                "_score": score,
            })

    results.sort(key=lambda x: (-x["_score"], x["path"]))
    for item in results:
        del item["_score"]
    return results[:max_results]


# ── kb_search ────────────────────────────────────────────────────────────

def search(
    kb_id: int,
    query: str,
    paths: list[str] | None = None,
    context_lines: int = 3,
    case_sensitive: bool = False,
    max_results: int = 15,
) -> list[dict[str, Any]]:
    root = _resolve_kb_root(kb_id)
    context_lines = max(0, min(context_lines, 10))

    args = ["rg", "--json", f"-C{context_lines}"]
    if not case_sensitive:
        args.append("-i")
    args.extend(["-e", query])

    if paths:
        for p in paths:
            target = root / p
            if target.exists():
                args.append(str(target))
            else:
                logger.warning("kb_search path not found: %s", target)
        if len(args) == len(["rg", "--json", f"-C{context_lines}", "-i", "-e", query]):
            return []
    else:
        args.append(str(root))

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_RG_TIMEOUT,
            cwd=str(Path.cwd()),
        )
    except subprocess.TimeoutExpired:
        logger.warning("kb_search timeout query=%s", query)
        return []
    except FileNotFoundError:
        args[0] = "grep"
        rg_args = _fallback_grep_args(query, root, paths, context_lines, case_sensitive)
        try:
            proc = subprocess.run(
                rg_args, capture_output=True, text=True, timeout=_RG_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    return _parse_rg_json_output(proc.stdout, root, max_results)


def _fallback_grep_args(
    query: str,
    root: Path,
    paths: list[str] | None,
    context_lines: int,
    case_sensitive: bool,
) -> list[str]:
    args = ["grep", "-rn", f"-C{context_lines}"]
    if not case_sensitive:
        args.append("-i")
    args.extend(["-e", query])
    if paths:
        for p in paths:
            target = root / p
            if target.exists():
                args.append(str(target))
    else:
        args.append(str(root))
    return args


def _parse_rg_json_output(
    stdout: str, root: Path, max_results: int,
) -> list[dict[str, Any]]:
    import json as _json

    results: list[dict[str, Any]] = []
    current_context_lines: list[str] = []
    current_match: dict[str, Any] | None = None

    for raw_line in stdout.splitlines()[:5000]:
        try:
            obj = _json.loads(raw_line)
        except _json.JSONDecodeError:
            continue

        msg_type = obj.get("type")
        if msg_type == "match":
            if current_match and len(results) < max_results:
                current_match["context"] = "\n".join(current_context_lines)
                results.append(current_match)
                current_context_lines = []

            data = obj.get("data", {})
            path_obj = data.get("path", {})
            abs_path = path_obj.get("text", "") if isinstance(path_obj, dict) else str(path_obj)
            try:
                rel = str(Path(abs_path).relative_to(root))
            except ValueError:
                rel = abs_path
            line_number = data.get("line_number", 0)
            lines = data.get("lines", {})
            match_text = lines.get("text", "").rstrip("\n") if isinstance(lines, dict) else str(lines).rstrip("\n")

            current_match = {
                "file": rel,
                "line": line_number,
                "match": match_text,
            }
            current_context_lines.append(match_text)

        elif msg_type == "context":
            data = obj.get("data", {})
            lines = data.get("lines", {})
            ctx_text = lines.get("text", "").rstrip("\n") if isinstance(lines, dict) else str(lines).rstrip("\n")
            current_context_lines.append(ctx_text)

    if current_match and len(results) < max_results:
        current_match["context"] = "\n".join(current_context_lines)
        results.append(current_match)

    return results


# ── kb_read ──────────────────────────────────────────────────────────────

def read(
    kb_id: int, path: str, start_line: int = 1, end_line: int = 100,
) -> dict[str, Any]:
    root = _resolve_kb_root(kb_id)
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path escapes knowledge base root: {path}")
    if not target.is_file():
        raise FileNotFoundError(f"Document not found: {path}")

    start_line = max(1, start_line)
    end_line = min(start_line + _MAX_READ_LINES - 1, end_line)

    lines: list[str] = []
    total_lines = 0
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            total_lines = i
            if start_line <= i <= end_line:
                lines.append(line.rstrip("\n"))

    return {
        "content": "\n".join(lines),
        "total_lines": total_lines,
        "start_line": start_line,
        "end_line": min(end_line, total_lines),
    }


# ── kb_outline ───────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


def outline(kb_id: int, path: str) -> list[dict[str, Any]]:
    root = _resolve_kb_root(kb_id)
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path escapes knowledge base root: {path}")
    if not target.is_file():
        raise FileNotFoundError(f"Document not found: {path}")

    headings: list[dict[str, Any]] = []
    in_code_block = False
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            m = _HEADING_RE.match(stripped)
            if m:
                headings.append({
                    "level": len(m.group(1)),
                    "title": m.group(2).strip(),
                    "line": line_num,
                })
    return headings


# ── Tool schemas & executor ──────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "kb_discover",
            "description": (
                "Find knowledge base documents by matching keywords against file names, "
                "directory names, and document titles. Use this FIRST to narrow scope "
                "before searching content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "query": {
                        "type": "string",
                        "description": "Space-separated keywords to match against file/dir names and titles",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum number of documents to return",
                    },
                },
                "required": ["kb_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": (
                "Search knowledge base document content with keywords or regex. "
                "Returns matching lines with surrounding context. "
                "Supports both Chinese and English content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "query": {
                        "type": "string",
                        "description": "Search keywords, phrase, or regex pattern",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Limit search to these file/directory paths (from kb_discover results)",
                    },
                    "context_lines": {
                        "type": "integer",
                        "default": 3,
                        "description": "Lines of context before and after each match (0-10)",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether the search is case-sensitive",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 15,
                        "description": "Maximum number of matches to return",
                    },
                },
                "required": ["kb_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_read",
            "description": (
                "Read a section of a knowledge base document by line range. "
                "Use after kb_search to get full context around a match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "path": {"type": "string", "description": "Document path relative to KB root"},
                    "start_line": {
                        "type": "integer",
                        "default": 1,
                        "description": "First line to read (1-based)",
                    },
                    "end_line": {
                        "type": "integer",
                        "default": 100,
                        "description": "Last line to read (max 200 lines per call)",
                    },
                },
                "required": ["kb_id", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_outline",
            "description": (
                "Get the heading structure of a markdown document. "
                "Use to understand document organization before deep-reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "path": {"type": "string", "description": "Document path relative to KB root"},
                },
                "required": ["kb_id", "path"],
            },
        },
    },
]

_TOOL_DISPATCH: dict[str, Any] = {
    "kb_discover": discover,
    "kb_search": search,
    "kb_read": read,
    "kb_outline": outline,
}


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"success": False, "error": f"Unknown tool: {name}"}
    try:
        result = fn(**arguments)
        return {"success": True, "data": result}
    except FileNotFoundError as e:
        return {"success": False, "error": {"code": "not_found", "message": str(e)}}
    except ValueError as e:
        return {"success": False, "error": {"code": "invalid_argument", "message": str(e)}}
    except Exception as e:
        logger.exception("kb_tool_error %s", fmt_kv(tool=name))
        return {"success": False, "error": {"code": "execution_error", "message": str(e)}}
