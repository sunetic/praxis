from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.config import DEFAULT_DATA_DIR
from app.core.logging import fmt_kv, get_logger
from app.services.knowledge.query_expansion import QueryPlan

logger = get_logger("knowledge.search_tools")

_DATA_ROOT = DEFAULT_DATA_DIR / "knowledge"
_SEARCH_TIMEOUT = 15
_FETCH_TIMEOUT = 300
_MAX_OUTPUT_BYTES = 300_000
_MAX_READ_LINES = 200
_KB_META_FILE = ".kb_meta.json"
_REPO_LOCKS_GUARD = threading.Lock()
_REPO_LOCKS: dict[Path, threading.Lock] = {}


@dataclass(frozen=True)
class SearchTarget:
    kb_id: int
    source_type: str
    root: Path
    pack_id: str | None = None
    db_type: str | None = None
    requested_version: str | None = None
    resolved_version: str | None = None
    repo: Path | None = None
    subdirectory: str = ""
    git_ref: str | None = None
    commit_sha: str | None = None

    def provenance(self) -> dict[str, Any]:
        return {
            "kb_id": self.kb_id,
            "pack_id": self.pack_id,
            "db_type": self.db_type,
            "source_type": self.source_type,
            "requested_version": self.requested_version,
            "resolved_version": self.resolved_version,
            "git_ref": self.git_ref,
            "commit_sha": self.commit_sha,
            "subdirectory": self.subdirectory,
        }


def read_kb_meta(kb_id: int) -> dict[str, Any] | None:
    meta_file = _DATA_ROOT / str(kb_id) / _KB_META_FILE
    if not meta_file.is_file():
        return None
    try:
        payload = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _kb_entries() -> Iterator[tuple[int, Path, dict[str, Any] | None]]:
    if not _DATA_ROOT.is_dir():
        return
    for entry in sorted(_DATA_ROOT.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        try:
            kb_id = int(entry.name)
        except ValueError:
            continue
        yield kb_id, entry, read_kb_meta(kb_id)


def _version_entries(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not meta or not isinstance(meta.get("versions"), list):
        return []
    return [item for item in meta["versions"] if isinstance(item, dict)]


def _find_version_entry(
    meta: dict[str, Any] | None,
    version: str,
) -> dict[str, Any] | None:
    target = version.casefold()
    for entry in _version_entries(meta):
        label = str(entry.get("label") or entry.get("branch") or "").strip()
        if label.casefold() == target:
            return entry
    return None


def _meta_supports_version(meta: dict[str, Any] | None, version: str) -> bool:
    entries = _version_entries(meta)
    if entries:
        return _find_version_entry(meta, version) is not None
    return bool(meta and str(meta.get("version") or "").casefold() == version.casefold())


def find_kb_by_db_type(db_type: str, version: str | None = None) -> int | None:
    normalized = str(db_type or "").strip().casefold()
    candidates: list[tuple[int, int]] = []
    for kb_id, path, meta in _kb_entries():
        if not meta or str(meta.get("db_type") or "").strip().casefold() != normalized:
            continue
        if version and not _meta_supports_version(meta, version):
            continue
        is_git = (path / ".git").is_dir()
        is_versioned = bool(_version_entries(meta) or meta.get("version"))
        rank = 0 if is_git and is_versioned else 1 if is_git else 2
        candidates.append((rank, kb_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _resolve_kb_root(kb_id: int) -> Path:
    base = _DATA_ROOT / str(kb_id)
    if not base.is_dir():
        raise FileNotFoundError(f"Knowledge base directory not found: {base}")
    meta = read_kb_meta(kb_id)
    if meta:
        local_path = meta.get("local_path")
        if local_path:
            path = Path(str(local_path)).resolve()
            if path.is_dir():
                return path
        subdirectory = str(meta.get("subdirectory") or "").strip()
        if subdirectory:
            root = (base / subdirectory).resolve()
            if root.is_dir():
                return root
    return base.resolve()


def _run_git(
    repo: Path,
    args: list[str],
    *,
    timeout: int = _SEARCH_TIMEOUT,
    allowed_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args[:2])} timed out") from exc
    if proc.returncode not in allowed_codes:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"git command failed with exit code {proc.returncode}")
    return proc


@contextmanager
def _repository_lock(repo: Path) -> Iterator[None]:
    git_dir = repo / ".git"
    lock_path = git_dir / "praxis-search.lock"
    repo_key = repo.resolve()
    with _REPO_LOCKS_GUARD:
        process_lock = _REPO_LOCKS.setdefault(repo_key, threading.Lock())
    with process_lock:
        lock_path.touch(exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            flock = None
            try:
                try:
                    import fcntl

                    flock = fcntl
                    flock.flock(lock_file.fileno(), flock.LOCK_EX)
                except ImportError:
                    pass
                yield
            finally:
                if flock is not None:
                    try:
                        flock.flock(lock_file.fileno(), flock.LOCK_UN)
                    except OSError:
                        pass


def _validate_source_ref(source_ref: str) -> None:
    if not source_ref.startswith(("refs/heads/", "refs/tags/")):
        raise ValueError(f"Unsupported knowledge pack git ref: {source_ref}")
    if any(token in source_ref for token in ("..", "@{", "\\", "~", "^", ":", "?", "*", "[")):
        raise ValueError(f"Invalid knowledge pack git ref: {source_ref}")


def _cache_ref(source_ref: str) -> str:
    digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:24]
    return f"refs/praxis/versions/{digest}"


def _rev_parse_commit(repo: Path, ref: str) -> str | None:
    proc = _run_git(
        repo,
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        allowed_codes=(0, 128),
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.decode("ascii", errors="ignore").strip()
    return value if re.fullmatch(r"[0-9a-fA-F]{40,64}", value) else None


def _materialize_commit(repo: Path, commit: str, subdirectory: str) -> None:
    marker_dir = repo / ".git" / "praxis-materialized"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_key = hashlib.sha256(f"{commit}:{subdirectory}".encode()).hexdigest()
    marker = marker_dir / marker_key
    if marker.is_file():
        return
    args = ["archive", "--format=tar", commit]
    if subdirectory:
        args.extend(["--", subdirectory])
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=_FETCH_TIMEOUT,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"Unable to materialize git commit {commit}")
    marker.touch()


def _resolve_git_snapshot(
    repo: Path,
    meta: dict[str, Any],
    requested_version: str | None,
) -> tuple[str | None, str, str | None]:
    resolved_version = requested_version or str(meta.get("version") or "").strip() or None
    subdirectory = str(meta.get("subdirectory") or "").strip().strip("/")

    if not resolved_version:
        with _repository_lock(repo):
            commit = _rev_parse_commit(repo, "HEAD")
            if not commit:
                raise RuntimeError(f"Knowledge repository has no valid HEAD: {repo}")
            _materialize_commit(repo, commit, subdirectory)
        return None, commit, None

    version_entry = _find_version_entry(meta, resolved_version)
    if requested_version and _version_entries(meta) and version_entry is None:
        raise ValueError(
            f"Version '{requested_version}' is not available for knowledge pack "
            f"'{meta.get('pack_id') or repo.name}'"
        )
    if (
        requested_version
        and not _version_entries(meta)
        and not _meta_supports_version(meta, requested_version)
    ):
        raise ValueError(
            f"Knowledge pack '{meta.get('pack_id') or repo.name}' does not advertise "
            f"version '{requested_version}'"
        )

    ref_type = str((version_entry or {}).get("ref_type") or "branch").strip().casefold()
    selector = str(
        (version_entry or {}).get("ref") or (version_entry or {}).get("branch") or resolved_version
    ).strip()
    if selector.startswith("refs/"):
        source_ref = selector
    elif ref_type == "tag":
        source_ref = f"refs/tags/{selector}"
    else:
        source_ref = f"refs/heads/{selector}"
    _validate_source_ref(source_ref)

    cached_ref = _cache_ref(source_ref)
    candidates = [cached_ref]
    if source_ref.startswith("refs/heads/"):
        branch = source_ref.removeprefix("refs/heads/")
        candidates.extend([f"refs/remotes/origin/{branch}", source_ref])
    else:
        candidates.append(source_ref)

    with _repository_lock(repo):
        commit = next(
            (candidate for ref in candidates if (candidate := _rev_parse_commit(repo, ref))),
            None,
        )
        if not commit:
            _run_git(
                repo,
                ["fetch", "--depth", "1", "origin", f"{source_ref}:{cached_ref}"],
                timeout=_FETCH_TIMEOUT,
            )
            commit = _rev_parse_commit(repo, cached_ref)
        if not commit:
            raise RuntimeError(
                f"Unable to resolve version '{resolved_version}' from git ref '{source_ref}'"
            )
        _materialize_commit(repo, commit, subdirectory)

    return source_ref, commit, resolved_version


def _resolve_search_target_sync(kb_id: int, version: str | None) -> SearchTarget:
    base = (_DATA_ROOT / str(kb_id)).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Knowledge base directory not found: {base}")
    meta = read_kb_meta(kb_id) or {}
    local_path = str(meta.get("local_path") or "").strip()
    if local_path:
        if version:
            raise ValueError(f"Knowledge base {kb_id} is not versioned; version cannot be used")
        root = Path(local_path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Knowledge base local path not found: {root}")
        return SearchTarget(
            kb_id=kb_id,
            source_type="filesystem",
            root=root,
            pack_id=meta.get("pack_id"),
            db_type=meta.get("db_type"),
        )

    if (base / ".git").is_dir():
        git_ref, commit, resolved_version = _resolve_git_snapshot(base, meta, version)
        return SearchTarget(
            kb_id=kb_id,
            source_type="git",
            root=base,
            repo=base,
            subdirectory=str(meta.get("subdirectory") or "").strip().strip("/"),
            pack_id=meta.get("pack_id"),
            db_type=meta.get("db_type"),
            requested_version=version,
            resolved_version=resolved_version,
            git_ref=git_ref,
            commit_sha=commit,
        )

    if version:
        raise ValueError(f"Knowledge base {kb_id} is not a git knowledge pack")
    return SearchTarget(
        kb_id=kb_id,
        source_type="filesystem",
        root=_resolve_kb_root(kb_id),
        pack_id=meta.get("pack_id"),
        db_type=meta.get("db_type"),
    )


async def resolve_search_targets(
    *,
    kb_ids: list[int] | None,
    db_type: str | None,
    version: str | None,
) -> list[SearchTarget]:
    resolved_ids = list(dict.fromkeys(int(value) for value in (kb_ids or [])))
    if not resolved_ids and db_type:
        resolved_kb_id = find_kb_by_db_type(db_type, version)
        if resolved_kb_id is None:
            suffix = f" and version='{version}'" if version else ""
            raise FileNotFoundError(f"No knowledge base found for db_type='{db_type}'{suffix}")
        resolved_ids = [resolved_kb_id]
    if not resolved_ids and version:
        resolved_ids = [
            kb_id
            for kb_id, path, meta in _kb_entries()
            if (path / ".git").is_dir() and _meta_supports_version(meta, version)
        ]
        if not resolved_ids:
            raise FileNotFoundError(f"No installed git knowledge pack supports version='{version}'")
    if not resolved_ids:
        resolved_ids = [kb_id for kb_id, _path, _meta in _kb_entries()]
    if not resolved_ids:
        return []

    targets = await asyncio.gather(
        *(asyncio.to_thread(_resolve_search_target_sync, kb_id, version) for kb_id in resolved_ids)
    )
    return list(targets)


def _normalize_relative_path(path: str) -> PurePosixPath:
    value = str(path or "").strip().replace("\\", "/")
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Invalid knowledge base path: {path}")
    return relative


def _filesystem_path(root: Path, path: str) -> Path:
    target = (root / _normalize_relative_path(path)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes knowledge base root: {path}")
    return target


def _repo_path(target: SearchTarget, path: str | None = None) -> str:
    base = PurePosixPath(target.subdirectory) if target.subdirectory else PurePosixPath()
    if path is None or not str(path).strip():
        return str(base)
    relative = _normalize_relative_path(path)
    return str(base / relative) if str(base) != "." else str(relative)


def _target_metadata(target: SearchTarget) -> dict[str, Any]:
    return {
        "kb_id": target.kb_id,
        "version": target.resolved_version,
        "commit_sha": target.commit_sha,
    }


def _git_list_markdown(target: SearchTarget) -> list[str]:
    if not target.repo or not target.commit_sha:
        raise ValueError("Git search target is incomplete")
    args = ["ls-tree", "-r", "-z", "--name-only", target.commit_sha]
    pathspec = _repo_path(target)
    if pathspec and pathspec != ".":
        args.extend(["--", pathspec])
    proc = _run_git(target.repo, args)
    prefix = f"{target.subdirectory}/" if target.subdirectory else ""
    results: list[str] = []
    for raw in proc.stdout.split(b"\0"):
        full_path = raw.decode("utf-8", errors="replace")
        if not full_path or not full_path.lower().endswith(".md"):
            continue
        relative = full_path.removeprefix(prefix)
        if PurePosixPath(relative).name.casefold() == "readme.md":
            continue
        results.append(relative)
    return sorted(results)


def _git_read_text(target: SearchTarget, path: str) -> str:
    if not target.repo or not target.commit_sha:
        raise ValueError("Git search target is incomplete")
    repo_path = _repo_path(target, path)
    proc = _run_git(target.repo, ["show", f"{target.commit_sha}:{repo_path}"])
    return proc.stdout.decode("utf-8", errors="replace")


def _parse_git_grep_records(output: bytes, commit: str) -> list[tuple[str, int, str]]:
    records: list[tuple[str, int, str]] = []
    cursor = 0
    prefix = f"{commit}:"
    while cursor < len(output):
        path_end = output.find(b"\0", cursor)
        if path_end < 0:
            break
        line_end = output.find(b"\0", path_end + 1)
        if line_end < 0:
            break
        content_end = output.find(b"\n", line_end + 1)
        if content_end < 0:
            break
        raw_path = output[cursor:path_end].decode("utf-8", errors="replace")
        raw_line = output[path_end + 1 : line_end].decode("ascii", errors="ignore")
        content = output[line_end + 1 : content_end].decode("utf-8", errors="replace")
        path = raw_path.removeprefix(prefix)
        try:
            line_number = int(raw_line)
        except ValueError:
            cursor = content_end + 1
            continue
        records.append((path, line_number, content))
        cursor = content_end + 1
    return records


def _git_grep(
    target: SearchTarget,
    patterns: list[str],
    *,
    paths: list[str] | None,
    case_sensitive: bool,
    max_results: int,
) -> list[tuple[str, int, str]]:
    if not target.repo or not target.commit_sha:
        raise ValueError("Git search target is incomplete")
    args = ["grep", "-z", "-n", "-I", "-E"]
    if not case_sensitive:
        args.append("-i")
    for pattern in patterns:
        args.extend(["-e", pattern])
    args.append(target.commit_sha)
    args.append("--")
    if paths:
        args.extend(_repo_path(target, path) for path in paths)
    else:
        root_path = _repo_path(target)
        if root_path and root_path != ".":
            args.append(root_path)
    proc = _run_git(target.repo, args, allowed_codes=(0, 1))
    if proc.returncode == 1:
        return []
    records = _parse_git_grep_records(proc.stdout[:_MAX_OUTPUT_BYTES], target.commit_sha)
    prefix = f"{target.subdirectory}/" if target.subdirectory else ""
    return [
        (path.removeprefix(prefix), line, content)
        for path, line, content in records[:max_results]
        if path.startswith(prefix)
    ]


def _extract_frontmatter_title(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            in_frontmatter = False
            for line in handle:
                stripped = line.strip()
                if stripped == "---" and not in_frontmatter:
                    in_frontmatter = True
                    continue
                if stripped == "---" and in_frontmatter:
                    break
                if in_frontmatter and stripped.casefold().startswith("title:"):
                    return stripped.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def discover(
    kb_id: int,
    query: str,
    max_results: int = 20,
    keywords: list[str] | None = None,
    *,
    target: SearchTarget | None = None,
) -> list[dict[str, Any]]:
    target = target or SearchTarget(
        kb_id=kb_id, source_type="filesystem", root=_resolve_kb_root(kb_id)
    )
    terms = [
        value.casefold()
        for value in [*re.split(r"[\s,|]+", query), *(keywords or [])]
        if str(value).strip()
    ]
    terms = list(dict.fromkeys(terms))
    if not terms:
        return []

    results: list[dict[str, Any]] = []
    if target.source_type == "git":
        files = _git_list_markdown(target)
        heading_records = _git_grep(
            target,
            [r"^(title[[:space:]]*:|#[[:space:]]+)"],
            paths=None,
            case_sensitive=False,
            max_results=max(len(files) * 3, 100),
        )
        titles: dict[str, str] = {}
        for path, _line, content in heading_records:
            if path in titles:
                continue
            value = content.strip()
            if value.casefold().startswith("title:"):
                value = value.split(":", 1)[1].strip().strip("'\"")
            else:
                value = value.lstrip("# ").strip()
            titles[path] = value
        for relative in files:
            title = titles.get(relative, "")
            searchable = f"{relative} {title}".casefold()
            score = sum(1 for term in terms if term in searchable)
            if score:
                results.append(
                    {
                        "path": relative,
                        "title": title or PurePosixPath(relative).stem,
                        "size_bytes": 0,
                        "_score": score,
                        **_target_metadata(target),
                    }
                )
    else:
        for md_path in sorted(target.root.rglob("*.md")):
            if md_path.name.casefold() == "readme.md":
                continue
            relative = str(md_path.relative_to(target.root))
            title = _extract_frontmatter_title(md_path)
            searchable = f"{relative} {title}".casefold()
            score = sum(1 for term in terms if term in searchable)
            if score:
                results.append(
                    {
                        "path": relative,
                        "title": title or md_path.stem,
                        "size_bytes": md_path.stat().st_size,
                        "_score": score,
                        **_target_metadata(target),
                    }
                )

    results.sort(key=lambda item: (-item["_score"], item["path"]))
    for item in results:
        del item["_score"]
    return results[:max_results]


def _normalize_patterns(query: str, patterns: list[str] | None) -> list[str]:
    values: list[str] = []
    for value in [query, *(patterns or [])]:
        text = str(value or "").strip()
        if text and text.casefold() not in {item.casefold() for item in values}:
            values.append(text)
    if not values:
        raise ValueError("At least one search query or pattern is required")
    return values[:80]


def _filesystem_search_paths(root: Path, paths: list[str] | None) -> list[Path]:
    if not paths:
        return [root]
    targets: list[Path] = []
    for path in paths:
        target = _filesystem_path(root, path)
        if not target.exists():
            raise FileNotFoundError(f"Knowledge base search path not found: {path}")
        targets.append(target)
    return targets


def search(
    kb_id: int,
    query: str = "",
    paths: list[str] | None = None,
    context_lines: int = 3,
    case_sensitive: bool = False,
    max_results: int = 15,
    patterns: list[str] | None = None,
    *,
    target: SearchTarget | None = None,
) -> list[dict[str, Any]]:
    target = target or SearchTarget(
        kb_id=kb_id, source_type="filesystem", root=_resolve_kb_root(kb_id)
    )
    normalized_patterns = _normalize_patterns(query, patterns)
    context_lines = max(0, min(context_lines, 10))

    if target.source_type == "git":
        records = _git_grep(
            target,
            normalized_patterns,
            paths=paths,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        file_cache: dict[str, list[str]] = {}
        results: list[dict[str, Any]] = []
        for path, line_number, match_text in records:
            lines = file_cache.setdefault(path, _git_read_text(target, path).splitlines())
            start = max(0, line_number - context_lines - 1)
            end = min(len(lines), line_number + context_lines)
            results.append(
                {
                    "file": path,
                    "line": line_number,
                    "match": match_text,
                    "context": "\n".join(lines[start:end]),
                    "patterns": normalized_patterns,
                    **_target_metadata(target),
                }
            )
        return results

    args = ["rg", "--json", f"-C{context_lines}"]
    if not case_sensitive:
        args.append("-i")
    for pattern in normalized_patterns:
        args.extend(["-e", pattern])
    args.extend(str(path) for path in _filesystem_search_paths(target.root, paths))
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_SEARCH_TIMEOUT,
            check=False,
        )
        if proc.returncode not in (0, 1):
            raise ValueError(proc.stderr.strip() or "Invalid knowledge search pattern")
        results = _parse_rg_json_output(proc.stdout[:_MAX_OUTPUT_BYTES], target.root, max_results)
    except FileNotFoundError:
        grep_args = ["grep", "-rHn"]
        if not case_sensitive:
            grep_args.append("-i")
        for pattern in normalized_patterns:
            grep_args.extend(["-e", pattern])
        grep_args.extend(str(path) for path in _filesystem_search_paths(target.root, paths))
        try:
            proc = subprocess.run(
                grep_args,
                capture_output=True,
                text=True,
                timeout=_SEARCH_TIMEOUT,
                check=False,
            )
            if proc.returncode not in (0, 1):
                raise ValueError(proc.stderr.strip() or "Invalid knowledge search pattern")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        results = _parse_grep_output(proc.stdout[:_MAX_OUTPUT_BYTES], target.root, max_results)
    except subprocess.TimeoutExpired:
        logger.warning("kb_search timeout patterns=%s", normalized_patterns[:5])
        return []

    for item in results:
        item.update(_target_metadata(target))
        item["patterns"] = normalized_patterns
    return results


def _parse_rg_json_output(stdout: str, root: Path, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    contexts: dict[tuple[str, int], list[str]] = {}
    matches: dict[tuple[str, int], dict[str, Any]] = {}
    active_key: tuple[str, int] | None = None
    for raw_line in stdout.splitlines()[:5000]:
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        message_type = obj.get("type")
        data = obj.get("data", {})
        path_obj = data.get("path", {})
        absolute = path_obj.get("text", "") if isinstance(path_obj, dict) else str(path_obj)
        try:
            relative = str(Path(absolute).relative_to(root))
        except ValueError:
            relative = absolute
        lines = data.get("lines", {})
        text = lines.get("text", "") if isinstance(lines, dict) else str(lines)
        text = text.rstrip("\n")
        if message_type == "match":
            active_key = (relative, int(data.get("line_number") or 0))
            matches[active_key] = {
                "file": relative,
                "line": active_key[1],
                "match": text,
            }
            contexts.setdefault(active_key, []).append(text)
        elif message_type == "context" and active_key:
            contexts.setdefault(active_key, []).append(text)
    for key, match in matches.items():
        match["context"] = "\n".join(contexts.get(key, []))
        results.append(match)
        if len(results) >= max_results:
            break
    return results


def _parse_grep_output(stdout: str, root: Path, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    pattern = re.compile(r"^(.*):(\d+):(.*)$")
    for line in stdout.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        path = Path(match.group(1))
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = str(path)
        results.append(
            {
                "file": relative,
                "line": int(match.group(2)),
                "match": match.group(3),
                "context": match.group(3),
            }
        )
        if len(results) >= max_results:
            break
    return results


def read(
    kb_id: int,
    path: str,
    start_line: int = 1,
    end_line: int = 100,
    *,
    target: SearchTarget | None = None,
) -> dict[str, Any]:
    target = target or SearchTarget(
        kb_id=kb_id, source_type="filesystem", root=_resolve_kb_root(kb_id)
    )
    if target.source_type == "git":
        content = _git_read_text(target, path)
    else:
        file_path = _filesystem_path(target.root, path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Document not found: {path}")
        content = file_path.read_text(encoding="utf-8", errors="replace")

    lines = content.splitlines()
    start_line = max(1, start_line)
    end_line = min(start_line + _MAX_READ_LINES - 1, end_line, len(lines))
    return {
        "file": path,
        "content": "\n".join(lines[start_line - 1 : end_line]),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end_line,
        **_target_metadata(target),
    }


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


def outline(
    kb_id: int,
    path: str,
    *,
    target: SearchTarget | None = None,
) -> list[dict[str, Any]]:
    target = target or SearchTarget(
        kb_id=kb_id, source_type="filesystem", root=_resolve_kb_root(kb_id)
    )
    if target.source_type == "git":
        content = _git_read_text(target, path)
    else:
        file_path = _filesystem_path(target.root, path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Document not found: {path}")
        content = file_path.read_text(encoding="utf-8", errors="replace")

    headings: list[dict[str, Any]] = []
    in_code_block = False
    for line_number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        match = _HEADING_RE.match(stripped)
        if match:
            headings.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "line": line_number,
                    **_target_metadata(target),
                }
            )
    return headings


def target_document_count(target: SearchTarget) -> int:
    if target.source_type == "git":
        return len(_git_list_markdown(target))
    return sum(1 for path in target.root.rglob("*.md") if path.name.casefold() != "readme.md")


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "kb_discover",
            "description": (
                "Find documents by file name, directory, and title. Start here, using all "
                "relevant original-language, English, identifier, and domain keyword variants."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "query": {"type": "string", "description": "Concise discovery terms"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional filename/title keyword variants",
                    },
                    "max_results": {"type": "integer", "default": 20},
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
                "Search document contents using one or more regular-expression patterns. "
                "Always include exact errors/codes plus bilingual and domain variants."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "query": {"type": "string", "description": "Primary search pattern"},
                    "patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional regex patterns; all are searched with OR semantics",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative files/directories from kb_discover",
                    },
                    "context_lines": {"type": "integer", "default": 3},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 15},
                },
                "required": ["kb_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_read",
            "description": "Read a document section after a search match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "path": {"type": "string", "description": "Relative document path"},
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {"type": "integer", "default": 100},
                },
                "required": ["kb_id", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_outline",
            "description": "Get Markdown headings before reading a long document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer", "description": "Knowledge base ID"},
                    "path": {"type": "string", "description": "Relative document path"},
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


class KnowledgeToolExecutor:
    def __init__(self, targets: list[SearchTarget], query_plan: QueryPlan) -> None:
        self._targets = {target.kb_id: target for target in targets}
        self._query_plan = query_plan
        self._searched_patterns: dict[int, set[str]] = {target.kb_id: set() for target in targets}
        self._expanded_targets: set[int] = set()

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            return {"success": False, "error": f"Unknown tool: {name}"}
        values = dict(arguments)
        values.pop("_runtime", None)
        try:
            kb_id = int(values.get("kb_id"))
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": {"code": "invalid_argument", "message": "kb_id is required"},
            }
        target = self._targets.get(kb_id)
        if target is None:
            return {
                "success": False,
                "error": {
                    "code": "scope_violation",
                    "message": f"Knowledge base {kb_id} is not part of this search snapshot",
                },
            }

        if name == "kb_discover" and kb_id not in self._expanded_targets:
            supplied = values.get("keywords") if isinstance(values.get("keywords"), list) else []
            values["keywords"] = list(dict.fromkeys([*supplied, *self._query_plan.discovery_terms]))
        effective_patterns: list[str] = []
        inject_plan = False
        if name == "kb_search":
            supplied = values.get("patterns") if isinstance(values.get("patterns"), list) else []
            effective = [str(values.get("query") or ""), *map(str, supplied)]
            inject_plan = kb_id not in self._expanded_targets
            if inject_plan:
                effective.extend(self._query_plan.all_patterns)
            effective_patterns = _normalize_patterns("", effective)
            values["patterns"] = effective_patterns
            values["query"] = ""

        try:
            result = await asyncio.to_thread(fn, **values, target=target)
            if name == "kb_search":
                if inject_plan:
                    self._expanded_targets.add(kb_id)
                self._searched_patterns[kb_id].update(
                    pattern.casefold() for pattern in effective_patterns
                )
            return {"success": True, "data": result}
        except FileNotFoundError as exc:
            return {"success": False, "error": {"code": "not_found", "message": str(exc)}}
        except ValueError as exc:
            return {
                "success": False,
                "error": {"code": "invalid_argument", "message": str(exc)},
            }
        except Exception as exc:
            logger.exception("kb_tool_error %s", fmt_kv(tool=name, kb_id=kb_id))
            return {
                "success": False,
                "error": {"code": "execution_error", "message": str(exc)},
            }

    def coverage_report(self) -> dict[str, Any]:
        searched_groups: dict[str, list[str]] = {}
        uncovered_groups: dict[str, list[str]] = {}
        target_coverage: dict[str, dict[str, Any]] = {}
        for kb_id, searched_patterns in self._searched_patterns.items():
            target_uncovered: dict[str, list[str]] = {}
            for name, patterns in self._query_plan.groups.items():
                missing = [
                    pattern for pattern in patterns if pattern.casefold() not in searched_patterns
                ]
                if missing:
                    target_uncovered[name] = missing
            target_coverage[str(kb_id)] = {
                "coverage_complete": bool(searched_patterns) and not target_uncovered,
                "uncovered_groups": target_uncovered,
                "searched_patterns": sorted(searched_patterns),
            }

        for name, patterns in self._query_plan.groups.items():
            if not patterns:
                continue
            searched = [
                pattern
                for pattern in patterns
                if all(
                    pattern.casefold() in target_patterns
                    for target_patterns in self._searched_patterns.values()
                )
            ]
            missing = [pattern for pattern in patterns if pattern not in searched]
            searched_groups[name] = searched
            if missing:
                uncovered_groups[name] = missing
        all_searched_patterns = {
            pattern
            for target_patterns in self._searched_patterns.values()
            for pattern in target_patterns
        }
        return {
            "searched_term_groups": searched_groups,
            "uncovered_groups": uncovered_groups,
            "coverage_complete": bool(target_coverage)
            and all(item["coverage_complete"] for item in target_coverage.values()),
            "searched_patterns": sorted(all_searched_patterns),
            "target_coverage": target_coverage,
        }


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"success": False, "error": f"Unknown tool: {name}"}
    values = dict(arguments)
    values.pop("_runtime", None)
    try:
        result = await asyncio.to_thread(fn, **values)
        return {"success": True, "data": result}
    except FileNotFoundError as exc:
        return {"success": False, "error": {"code": "not_found", "message": str(exc)}}
    except ValueError as exc:
        return {"success": False, "error": {"code": "invalid_argument", "message": str(exc)}}
    except Exception as exc:
        logger.exception("kb_tool_error %s", fmt_kv(tool=name))
        return {
            "success": False,
            "error": {"code": "execution_error", "message": str(exc)},
        }
