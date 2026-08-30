"""Shared real-service runtime for database DBA Eval suites."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

import httpx

from app.core.config import DEFAULT_SQLITE_DB_PATH, Settings
from evals.dba_core.catalog import EvalCase, EvalCatalog, load_catalog
from evals.dba_core.reporting import render_markdown, write_reports
from evals.dba_core.scoring import (
    aggregate_scores,
    provider_available,
    score_case,
    terminal_metrics,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = PROJECT_ROOT / ".artifacts" / "evals"


@dataclass(frozen=True)
class LLMConfig:
    """Resolved local executor configuration; the key must never be serialized."""

    base_url: str
    api_key: str
    model: str
    source: str


@dataclass
class ChildProcess:
    """A managed child process and its open log file."""

    process: subprocess.Popen[str]
    log_file: TextIO

    def stop(self) -> None:
        """Stop the child and close its log file."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()


class DatabaseFixture(Protocol):
    """Engine-specific fixture operations required by the shared runtime."""

    engine: str
    container_prefix: str
    fixture_module: str

    def start(self, container: str, port: int, image: str) -> None:
        """Start the isolated database and wait until it accepts connections."""

    def seed(self, container: str, workload_repeats: int) -> None:
        """Build the deterministic fixture and statistical workload."""

    def snapshot(self, container: str) -> dict[str, Any]:
        """Capture data and security invariants used by the safety gate."""

    def datasource_payload(self, port: int) -> dict[str, Any]:
        """Return the payload for the real Praxis datasource API."""

    def process_environment(self, port: int) -> dict[str, str]:
        """Return environment variables for the live session fixture."""

    def execute_readonly(self, container: str, sql: str) -> str:
        """Execute SQL after the fixed model harness has validated it as read-only."""


@dataclass(frozen=True)
class SuiteDefinition:
    """Static metadata and knowledge fixture for one DBA suite."""

    key: str
    cli_description: str
    image_option: str
    default_image: str
    report_title: str
    catalog_path: Path
    artifact_subdirectory: str | None
    knowledge_name: str
    knowledge_description: str
    knowledge_tags: tuple[str, ...]
    knowledge_policy_path: Path


@dataclass(frozen=True)
class SuiteRunner:
    """Bind one engine-specific fixture to the shared DBA Eval runtime."""

    suite: SuiteDefinition
    fixture: DatabaseFixture

    def load_catalog(self) -> EvalCatalog:
        """Load this suite's versioned case catalog."""
        return load_catalog(self.suite.catalog_path)

    def render_report(
        self,
        summary: dict[str, Any],
        baseline: dict[str, Any] | None = None,
    ) -> str:
        """Render this suite's Markdown scorecard."""
        return render_markdown(summary, baseline, title=self.suite.report_title)

    def build_parser(self) -> argparse.ArgumentParser:
        """Build this suite's command-line parser."""
        parser = argparse.ArgumentParser(description=self.suite.cli_description)
        add_common_arguments(
            parser,
            self.load_catalog(),
            image_option=self.suite.image_option,
            default_image=self.suite.default_image,
        )
        return parser

    def run(self, args: argparse.Namespace) -> int:
        """Execute this suite and return its classified exit code."""
        return run_eval(args, suite=self.suite, fixture=self.fixture)

    def main(self) -> None:
        """Run this suite as a command-line entry point."""
        run_main(self.build_parser(), suite=self.suite, fixture=self.fixture)


def _decode_json_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        return str(decoded) if decoded is not None else ""
    return str(value)


def resolve_llm_config(settings_db: Path | None = None) -> LLMConfig:
    """Resolve platform settings first, then fall back to local env/.env settings."""
    local = Settings()
    values = {
        "ai_base_url": local.ai_base_url,
        "ai_api_key": local.ai_api_key,
        "ai_model": local.ai_model,
    }
    source = "environment"
    db_path = (settings_db or DEFAULT_SQLITE_DB_PATH).expanduser().resolve()
    if settings_db is not None and not db_path.is_file():
        raise RuntimeError(f"Eval settings database does not exist: {db_path}")
    if db_path.is_file():
        try:
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT key, value FROM platform_settings "
                    "WHERE key IN ('ai_base_url', 'ai_api_key', 'ai_model')"
                ).fetchall()
            overrides = {str(key): _decode_json_value(value) for key, value in rows}
            if any(overrides.values()):
                values.update({key: value for key, value in overrides.items() if value})
                source = "platform_settings"
        except sqlite3.Error as exc:
            if settings_db is not None:
                raise RuntimeError(f"Unable to read Eval settings database: {db_path}") from exc
            print(
                "[eval] warning: local platform settings unavailable "
                f"({type(exc).__name__}); falling back to environment/.env",
                file=sys.stderr,
                flush=True,
            )
    config = LLMConfig(
        base_url=values["ai_base_url"].rstrip("/"),
        api_key=values["ai_api_key"],
        model=values["ai_model"],
        source=source,
    )
    missing = [
        name
        for name, value in (
            ("AI_BASE_URL", config.base_url),
            ("AI_API_KEY", config.api_key),
            ("AI_MODEL", config.model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing local LLM configuration: {', '.join(missing)}")
    return config


def require_expected_model(config: LLMConfig, expected_model: str | None) -> None:
    """Refuse a formal run when configuration resolved to another model."""
    if expected_model and config.model != expected_model:
        raise RuntimeError(
            "Resolved model does not match --expected-model "
            f"({config.model!r} != {expected_model!r}); refusing to run"
        )


def free_port() -> int:
    """Reserve and return an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_id() -> str:
    """Return a sortable UTC run identifier."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def git_commit() -> str:
    """Return the current short commit without failing outside Git."""
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def git_worktree_dirty() -> bool | None:
    """Return whether tracked or untracked work differs from the recorded commit."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def probe_provider(config: LLMConfig, attempts: int = 4) -> dict[str, Any]:
    """Probe the configured chat-completions endpoint with transient retries."""
    endpoint = f"{config.base_url}/chat/completions"
    started = time.monotonic()
    last_error = ""
    attempts_used = 0
    with httpx.Client(timeout=30, trust_env=False) as client:
        for attempt in range(1, attempts + 1):
            attempts_used = attempt
            try:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {config.api_key}"},
                    json={
                        "model": config.model,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "max_tokens": 8,
                        "temperature": 0,
                        "stream": False,
                    },
                )
                if response.status_code < 400:
                    return {
                        "ok": True,
                        "attempts": attempt,
                        "latency_seconds": round(time.monotonic() - started, 3),
                    }
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(min(2**attempt, 10))
    return {
        "ok": False,
        "attempts": attempts_used,
        "latency_seconds": round(time.monotonic() - started, 3),
        "error": last_error or "unknown provider error",
    }


def docker(*args: str, input_text: str | None = None, timeout: float = 120) -> str:
    """Run Docker with checked errors and bounded output."""
    result = subprocess.run(
        ["docker", *args],
        cwd=PROJECT_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {message}")
    return result.stdout.strip()


def start_logged_process(
    command: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
) -> ChildProcess:
    """Start a child process whose combined output is persisted for audit."""
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ChildProcess(process=process, log_file=log_file)


def wait_for_http(url: str, process: subprocess.Popen[str], timeout: float = 90) -> None:
    """Wait for a real HTTP service while also detecting early process exit."""
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Backend exited early with code {process.returncode}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise RuntimeError("Praxis backend did not become ready")


def wait_for_fixture(log_path: Path, process: subprocess.Popen[str], timeout: float = 60) -> None:
    """Wait until the engine-specific session fixture confirms readiness."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Session fixture exited early with code {process.returncode}")
        if log_path.is_file() and "fixture_ready" in log_path.read_text(encoding="utf-8"):
            return
        time.sleep(0.25)
    raise RuntimeError("Session fixture did not become ready")


def _create_runtime_resources(
    api: str,
    data_dir: Path,
    datasource_payload: dict[str, Any],
    suite: SuiteDefinition,
) -> tuple[int, int]:
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(f"{api}/datasources", json=datasource_payload)
        response.raise_for_status()
        datasource_id = int(response.json()["id"])
        test_response = client.post(f"{api}/datasources/{datasource_id}/test")
        test_response.raise_for_status()
        if not test_response.json().get("success"):
            raise RuntimeError(f"Saved datasource connection failed: {test_response.json()}")

        response = client.post(
            f"{api}/knowledge-bases",
            json={
                "name": suite.knowledge_name,
                "description": suite.knowledge_description,
                "tags": list(suite.knowledge_tags),
            },
        )
        response.raise_for_status()
        kb_id = int(response.json()["id"])
        policy = suite.knowledge_policy_path
        with policy.open("rb") as source:
            upload = client.post(
                f"{api}/knowledge-bases/{kb_id}/documents",
                files={"file": (policy.name, source, "text/markdown")},
            )
        upload.raise_for_status()
        kb_meta = data_dir / "knowledge" / str(kb_id) / ".kb_meta.json"
        kb_meta.write_text(
            json.dumps(
                {
                    "db_type": suite.key,
                    "source_type": "local_eval",
                    "name": suite.knowledge_name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return datasource_id, kb_id


def _parse_sse_line(line: str) -> dict[str, Any] | str:
    body = line[5:].strip() if line.startswith("data:") else line
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body
    return parsed if isinstance(parsed, dict) else body


async def _collect_case_stream(
    url: str,
    payload: dict[str, Any],
    case_timeout: float,
) -> tuple[list[dict[str, Any] | str], int | None, str | None, bool]:
    """Collect one Chat stream under a total case deadline.

    HTTPX read timeouts measure inactivity between response chunks, so they do
    not cap a long-lived SSE request that continues to emit events.  The Eval
    option is a total case budget and therefore needs an outer asyncio
    deadline that also cancels the stream when the budget expires.
    """
    stream_items: list[dict[str, Any] | str] = []
    stream_status: int | None = None
    stream_error: str | None = None
    case_timed_out = False
    transport_timeout = httpx.Timeout(connect=min(10.0, case_timeout), read=None, write=30, pool=30)
    try:
        async with httpx.AsyncClient(timeout=transport_timeout, trust_env=False) as client:
            async with asyncio.timeout(case_timeout):
                async with client.stream("POST", url, json=payload) as stream:
                    stream_status = stream.status_code
                    stream.raise_for_status()
                    async for line in stream.aiter_lines():
                        if line and line.startswith("data:"):
                            stream_items.append(_parse_sse_line(line))
    except TimeoutError:
        case_timed_out = True
    except Exception as exc:
        stream_error = f"{type(exc).__name__}: {exc}"
    return stream_items, stream_status, stream_error, case_timed_out


def _run_case(
    api: str,
    case: EvalCase,
    datasource_id: int,
    attempt: int,
    output_dir: Path,
    case_timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(
            f"{api}/conversations",
            json={
                "title": f"DBA Eval {case.case_id} attempt {attempt}",
                "datasource_id": datasource_id,
                "active_skills": [],
                "category": "primary",
                "read_only": True,
            },
        )
        response.raise_for_status()
        conversation = response.json()
        conversation_id = int(conversation["id"])
        stream_items, stream_status, stream_error, case_timed_out = asyncio.run(
            _collect_case_stream(
                f"{api}/chat/{conversation_id}/stream",
                {"content": case.prompt, "locale": "zh-CN"},
                case_timeout,
            )
        )
        messages_response = client.get(f"{api}/messages/conversation/{conversation_id}")
        events_response = client.get(f"{api}/chat/{conversation_id}/events")
        messages_response.raise_for_status()
        events_response.raise_for_status()
    evidence: dict[str, Any] = {
        "case_id": case.case_id,
        "title": case.title,
        "prompt": case.prompt,
        "attempt": attempt,
        "conversation_id": conversation_id,
        "stream_http_status": stream_status,
        "stream_error": stream_error,
        "case_timed_out": case_timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stream": stream_items,
        "messages": messages_response.json(),
        "events": events_response.json(),
    }
    score = score_case(case, evidence).to_dict()
    runtime_metrics = terminal_metrics(evidence)
    evidence["score"] = score
    evidence_path = output_dir / "evidence" / f"{case.case_id}-attempt-{attempt}.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[eval] {case.case_id} attempt={attempt} status={score['status']} "
        f"reliability={score['reliability_score']} outcome={score['outcome_score']} "
        f"quality={score['answer_quality_score']} evidence={score['evidence_score']} "
        f"seconds={evidence['duration_seconds']}",
        flush=True,
    )
    return {
        "case_id": case.case_id,
        "title": case.title,
        "attempt": attempt,
        "conversation_id": conversation_id,
        "duration_seconds": evidence["duration_seconds"],
        "evidence": str(evidence_path.relative_to(output_dir)),
        "provider_available": provider_available(evidence),
        "runtime_metrics": runtime_metrics,
        "score": score,
    }


def _run_model_case(
    *,
    config: LLMConfig,
    fixture: DatabaseFixture,
    container: str,
    suite: SuiteDefinition,
    case: EvalCase,
    attempt: int,
    output_dir: Path,
    case_timeout: float,
    max_tool_rounds: int,
) -> dict[str, Any]:
    """Run a candidate through the stable harness and persist comparable evidence."""
    from evals.dba_core.model_harness import run_case

    result = run_case(
        config=config,
        fixture=fixture,
        container=container,
        case=case,
        policy_path=suite.knowledge_policy_path,
        attempt=attempt,
        max_tool_rounds=max_tool_rounds,
        timeout=case_timeout,
    )
    evidence = result.evidence
    evidence_path = output_dir / "evidence" / f"{case.case_id}-attempt-{attempt}.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    score = evidence["score"]
    print(
        f"[eval] {case.case_id} attempt={attempt} profile=model status={score['status']} "
        f"outcome={score['outcome_score']} quality={score['answer_quality_score']} "
        f"evidence={score['evidence_score']} seconds={evidence['duration_seconds']}",
        flush=True,
    )
    return {
        "case_id": case.case_id,
        "title": case.title,
        "attempt": attempt,
        "duration_seconds": evidence["duration_seconds"],
        "evidence": str(evidence_path.relative_to(output_dir)),
        "provider_available": evidence["stream_http_status"] == 200
        and evidence["stream_error"] is None,
        "runtime_metrics": result.runtime_metrics,
        "score": score,
    }


def _load_baseline(
    path: Path | None,
    catalog: EvalCatalog,
    profile: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if payload.get("suite") != catalog.suite or payload.get("suite_version") != catalog.version:
        raise ValueError(
            "Baseline suite/version does not match the selected Eval catalog "
            f"({catalog.suite}@{catalog.version})"
        )
    if payload.get("profile", "praxis") != profile:
        raise ValueError("Baseline profile does not match the selected Eval profile")
    return payload


def _initial_summary(
    *,
    current_run_id: str,
    commit: str,
    catalog: EvalCatalog,
    config: LLMConfig,
    started_at: str,
    profile: str,
) -> dict[str, Any]:
    return {
        "run_id": current_run_id,
        "commit": commit,
        "working_tree_dirty": git_worktree_dirty(),
        "suite": catalog.suite,
        "suite_version": catalog.version,
        "profile": profile,
        "model": config.model,
        "llm_config_source": config.source,
        "started_at": started_at,
        "provider_probe": {},
        "provider_availability": 0.0,
        "environment_unchanged": False,
        "results": [],
        "aggregate": aggregate_scores([], False),
    }


def add_common_arguments(
    parser: argparse.ArgumentParser,
    catalog: EvalCatalog,
    *,
    image_option: str,
    default_image: str,
) -> None:
    """Add the stable cross-engine CLI contract to a suite parser."""
    parser.add_argument("--case", choices=["all", *catalog.by_id()], default="all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--settings-db", type=Path)
    parser.add_argument(
        "--expected-model",
        help="Fail before contacting the provider unless the resolved model matches exactly",
    )
    parser.add_argument(image_option, dest="database_image", default=default_image)
    parser.add_argument("--workload-repeats", type=int, default=8)
    parser.add_argument("--case-timeout", type=float, default=300)
    parser.add_argument("--case-delay", type=float, default=3)
    parser.add_argument(
        "--profile",
        choices=("praxis", "model"),
        default="praxis",
        help="Evaluate the complete Praxis system or compare a model with the fixed harness",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=20,
        help="Maximum fixed-harness tool rounds for --profile model",
    )
    parser.add_argument("--list-cases", action="store_true")


def run_eval(
    args: argparse.Namespace,
    *,
    suite: SuiteDefinition,
    fixture: DatabaseFixture,
) -> int:
    """Execute a full isolated Eval run and return a classified shell exit code."""
    catalog = load_catalog(suite.catalog_path)
    if args.list_cases:
        for case in catalog.cases:
            print(f"{case.case_id}\t{case.title}")
        return 0
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    if args.workload_repeats < 1:
        raise ValueError("--workload-repeats must be at least 1")
    if args.case_timeout <= 0:
        raise ValueError("--case-timeout must be positive")
    if args.case_delay < 0:
        raise ValueError("--case-delay cannot be negative")
    if args.max_tool_rounds < 1:
        raise ValueError("--max-tool-rounds must be at least 1")
    if shutil.which("docker") is None:
        raise RuntimeError(f"Docker is required for the isolated {fixture.engine} fixture")

    config = resolve_llm_config(args.settings_db)
    require_expected_model(config, args.expected_model)

    current_run_id = run_id()
    default_root = (
        ARTIFACT_ROOT / suite.artifact_subdirectory
        if suite.artifact_subdirectory
        else ARTIFACT_ROOT
    )
    output_dir = (args.output or default_root / current_run_id).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC).isoformat()
    summary = _initial_summary(
        current_run_id=current_run_id,
        commit=git_commit(),
        catalog=catalog,
        config=config,
        started_at=started_at,
        profile=args.profile,
    )
    summary["run_config"] = {
        "case": args.case,
        "repeat": args.repeat,
        "case_timeout_seconds": args.case_timeout,
        "case_delay_seconds": args.case_delay,
        "workload_repeats": args.workload_repeats,
        "max_tool_rounds": args.max_tool_rounds if args.profile == "model" else None,
        "database_image": args.database_image,
    }
    baseline = _load_baseline(args.baseline, catalog, args.profile)
    print(
        f"[eval] run={current_run_id} commit={summary['commit']} model={config.model} "
        f"config={config.source} suite={catalog.suite} "
        f"profile={args.profile}",
        flush=True,
    )
    probe = probe_provider(config)
    summary["provider_probe"] = probe
    if not probe["ok"]:
        summary["startup_error"] = f"Provider probe failed: {probe.get('error', 'unknown')}"
        write_reports(output_dir, summary, baseline, title=suite.report_title)
        print(f"[eval] provider unavailable; report={output_dir / 'report.md'}", flush=True)
        return 2

    database_port = free_port()
    backend_port = free_port()
    container = f"{fixture.container_prefix}-{current_run_id.lower()}-{os.getpid()}"
    backend: ChildProcess | None = None
    session_fixture: ChildProcess | None = None
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    database_started = False
    results: list[dict[str, Any]] = []
    runtime_error: str | None = None
    try:
        print(
            f"[eval] starting isolated {fixture.engine} on 127.0.0.1:{database_port}...",
            flush=True,
        )
        fixture.start(container, database_port, args.database_image)
        database_started = True
        fixture.seed(container, args.workload_repeats)
        before = fixture.snapshot(container)

        runtime_dir = output_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        fixture_log = output_dir / "fixture.log"
        fixture_env = os.environ.copy()
        fixture_env.update(fixture.process_environment(database_port))
        fixture_env["PYTHONUNBUFFERED"] = "1"
        session_fixture = start_logged_process(
            [sys.executable, "-m", fixture.fixture_module],
            fixture_log,
            env=fixture_env,
        )
        wait_for_fixture(fixture_log, session_fixture.process)

        api: str | None = None
        datasource_id: int | None = None
        if args.profile == "praxis":
            backend_env = os.environ.copy()
            backend_env.update(
                {
                    "DATABASE_URL": f"sqlite:///{runtime_dir / 'praxis.db'}",
                    "DATA_DIR": str(runtime_dir / "data"),
                    "TRACING_DB_PATH": str(runtime_dir / "tracing.db"),
                    "TRACING_ENABLED": "false",
                    "SCHEDULER_AUTOSTART": "false",
                    "AI_BASE_URL": config.base_url,
                    "AI_API_KEY": config.api_key,
                    "AI_MODEL": config.model,
                    "PRAXIS_WORKSPACE_ROOT": str(runtime_dir / "workspace"),
                    "AGENT_MAX_ELAPSED_SECONDS": str(args.case_timeout),
                    "PYTHONUNBUFFERED": "1",
                }
            )
            backend = start_logged_process(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                ],
                output_dir / "backend.log",
                env=backend_env,
            )
            wait_for_http(f"http://127.0.0.1:{backend_port}/health", backend.process)
            api = f"http://127.0.0.1:{backend_port}/api/v1"
            datasource_id, _ = _create_runtime_resources(
                api,
                runtime_dir / "data",
                fixture.datasource_payload(database_port),
                suite,
            )
            print(
                f"[eval] real Praxis backend ready on 127.0.0.1:{backend_port}",
                flush=True,
            )
        else:
            print("[eval] fixed model-comparison harness ready", flush=True)

        selected = catalog.cases if args.case == "all" else (catalog.by_id()[args.case],)
        for case_index, case in enumerate(selected):
            for attempt in range(1, args.repeat + 1):
                if args.profile == "model":
                    results.append(
                        _run_model_case(
                            config=config,
                            fixture=fixture,
                            container=container,
                            suite=suite,
                            case=case,
                            attempt=attempt,
                            output_dir=output_dir,
                            case_timeout=args.case_timeout,
                            max_tool_rounds=args.max_tool_rounds,
                        )
                    )
                else:
                    if api is None or datasource_id is None:
                        raise RuntimeError("Praxis profile resources were not initialized")
                    results.append(
                        _run_case(
                            api,
                            case,
                            datasource_id,
                            attempt,
                            output_dir,
                            args.case_timeout,
                        )
                    )
                has_more_attempts = attempt < args.repeat or case_index < len(selected) - 1
                if args.case_delay > 0 and has_more_attempts:
                    time.sleep(args.case_delay)
        after = fixture.snapshot(container)
    except Exception as exc:
        runtime_error = f"{type(exc).__name__}: {exc}"
        print(f"[eval] runtime failure: {runtime_error}", flush=True)
    finally:
        if session_fixture is not None:
            session_fixture.stop()
        if backend is not None:
            backend.stop()
        if database_started:
            subprocess.run(
                ["docker", "stop", "--time", "5", container],
                capture_output=True,
                text=True,
                check=False,
            )

    unchanged = bool(before) and before == after
    successful_streams = sum(1 for item in results if item.get("provider_available"))
    summary.update(
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "provider_availability": round(successful_streams / len(results), 4)
            if results
            else 0.0,
            "environment_unchanged": unchanged,
            "database_before": before,
            "database_after": after,
            "results": results,
            "aggregate": aggregate_scores(results, unchanged),
        }
    )
    if runtime_error:
        summary["runtime_error"] = runtime_error
    json_path, report_path = write_reports(
        output_dir,
        summary,
        baseline,
        title=suite.report_title,
    )
    print(f"[eval] report={report_path}", flush=True)
    print(f"[eval] machine_summary={json_path}", flush=True)
    aggregate = summary["aggregate"]
    if runtime_error:
        return 2
    if not aggregate["safety_passed"]:
        return 3
    if aggregate["status_counts"].get("infra_fail") or aggregate["status_counts"].get("incomplete"):
        return 2
    return 0 if aggregate["pass_rate"] == 1.0 else 1


def run_main(
    parser: argparse.ArgumentParser,
    *,
    suite: SuiteDefinition,
    fixture: DatabaseFixture,
) -> None:
    """Run one suite CLI with stable error-to-exit-code handling."""
    try:
        code = run_eval(parser.parse_args(), suite=suite, fixture=fixture)
    except (RuntimeError, ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"eval failed: {exc}\n")
    raise SystemExit(code)
