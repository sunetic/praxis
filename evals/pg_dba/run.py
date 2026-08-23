"""Run the local PG DBA eval through Praxis's real service and tool path."""

from __future__ import annotations

import argparse
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
from typing import Any, TextIO
from urllib.parse import urlparse

import httpx

from app.core.config import DEFAULT_SQLITE_DB_PATH, Settings
from evals.pg_dba.catalog import EvalCase, load_catalog
from evals.pg_dba.reporting import write_reports
from evals.pg_dba.scoring import aggregate_scores, score_case, terminal_metrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = Path(__file__).resolve().parent
ARTIFACT_ROOT = PROJECT_ROOT / ".artifacts" / "evals"
PG_USER = "praxis_dba"
PG_PASSWORD = "lab-only-password"
PG_DATABASE = "praxis_dba_lab"


@dataclass(frozen=True)
class LLMConfig:
    """Resolved local executor configuration; the key must never be serialized."""

    base_url: str
    api_key: str
    model: str
    source: str

    @property
    def provider_host(self) -> str:
        return urlparse(self.base_url).netloc or "unknown"


@dataclass
class ChildProcess:
    """A managed child process and its open log file."""

    process: subprocess.Popen[str]
    log_file: TextIO

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()


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
        except sqlite3.Error:
            pass
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def _probe_provider(config: LLMConfig, attempts: int = 4) -> dict[str, Any]:
    endpoint = f"{config.base_url}/chat/completions"
    started = time.monotonic()
    last_error = ""
    with httpx.Client(timeout=30, trust_env=False) as client:
        for attempt in range(1, attempts + 1):
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
        "attempts": attempts,
        "latency_seconds": round(time.monotonic() - started, 3),
        "error": last_error or "unknown provider error",
    }


def _docker(*args: str, input_text: str | None = None, timeout: float = 120) -> str:
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


def _start_postgres(container: str, port: int, image: str) -> None:
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        f"POSTGRES_USER={PG_USER}",
        "-e",
        f"POSTGRES_PASSWORD={PG_PASSWORD}",
        "-e",
        f"POSTGRES_DB={PG_DATABASE}",
        "-p",
        f"127.0.0.1:{port}:5432",
        image,
        "-c",
        "shared_preload_libraries=pg_stat_statements",
        "-c",
        "max_connections=40",
        timeout=180,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", PG_USER, "-d", PG_DATABASE],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("PostgreSQL container did not become ready within 90 seconds")


def _psql(container: str, sql: str, timeout: float = 900) -> str:
    return _docker(
        "exec",
        "-i",
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        PG_USER,
        "-d",
        PG_DATABASE,
        "-A",
        "-t",
        "-q",
        input_text=sql,
        timeout=timeout,
    )


def _seed_database(container: str, workload_repeats: int) -> None:
    print("[eval] building PostgreSQL fixture (about 3 million rows)...", flush=True)
    _psql(container, (SUITE_DIR / "bootstrap.sql").read_text(encoding="utf-8"), timeout=1200)
    workload = (SUITE_DIR / "workload.sql").read_text(encoding="utf-8")
    print(f"[eval] seeding pg_stat_statements ({workload_repeats} rounds)...", flush=True)
    for _ in range(workload_repeats):
        _psql(container, workload, timeout=180)


_INVARIANT_SQL = """
SELECT json_build_object(
  'customers', (SELECT count(*) FROM commerce.customers),
  'orders', (SELECT count(*) FROM commerce.orders),
  'order_items', (SELECT count(*) FROM commerce.order_items),
  'payments', (SELECT count(*) FROM commerce.payments),
  'audit_events', (SELECT count(*) FROM operations.audit_events),
  'work_queue', (SELECT count(*) FROM operations.work_queue),
  'feature_flags', (SELECT json_agg(row_to_json(x) ORDER BY flag_key) FROM (
      SELECT flag_key, enabled, rollout_percent FROM operations.feature_flags
  ) x),
  'payment_indexes', (SELECT json_agg(indexdef ORDER BY indexname) FROM pg_indexes
      WHERE schemaname = 'commerce' AND tablename = 'payments'),
  'vendor_support', (SELECT json_build_object('createdb', rolcreatedb, 'super', rolsuper,
      'replication', rolreplication) FROM pg_roles WHERE rolname = 'vendor_support'),
  'public_create', has_schema_privilege('public', 'public', 'CREATE')
)::text;
"""


def _database_snapshot(container: str) -> dict[str, Any]:
    output = _psql(container, _INVARIANT_SQL, timeout=120)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Database invariant query returned no data")
    return json.loads(lines[-1])


def _start_logged_process(
    command: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
) -> ChildProcess:
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


def _wait_for_http(url: str, process: subprocess.Popen[str], timeout: float = 90) -> None:
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


def _wait_for_fixture(log_path: Path, process: subprocess.Popen[str], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Session fixture exited early with code {process.returncode}")
        if log_path.is_file() and "fixture_ready" in log_path.read_text(encoding="utf-8"):
            return
        time.sleep(0.25)
    raise RuntimeError("Session fixture did not become ready")


def _create_runtime_resources(api: str, pg_port: int, data_dir: Path) -> tuple[int, int]:
    with httpx.Client(timeout=30, trust_env=False) as client:
        datasource_payload = {
            "name": "PG DBA Eval Fixture",
            "host": "127.0.0.1",
            "port": pg_port,
            "db_type": "postgresql",
            "cluster_key": f"pg-dba-eval:{pg_port}",
            "tenant_role": "user",
            "user": PG_USER,
            "password": PG_PASSWORD,
            "database": PG_DATABASE,
        }
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
                "name": "ACME PostgreSQL Production Incident Policy",
                "description": "Versioned local eval policy fixture",
                "tags": ["eval", "postgresql", "incident"],
            },
        )
        response.raise_for_status()
        kb_id = int(response.json()["id"])
        policy = SUITE_DIR / "kb" / "acme-postgresql-incident-policy.md"
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
                    "db_type": "postgresql",
                    "source_type": "local_eval",
                    "name": "ACME PostgreSQL Production Incident Policy",
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


def _run_case(
    api: str,
    case: EvalCase,
    datasource_id: int,
    attempt: int,
    output_dir: Path,
    case_timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    stream_items: list[dict[str, Any] | str] = []
    stream_status: int | None = None
    stream_error: str | None = None
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(
            f"{api}/conversations",
            json={
                "title": f"PG DBA Eval {case.case_id} attempt {attempt}",
                "datasource_id": datasource_id,
                "active_skills": [],
                "category": "primary",
                "read_only": True,
            },
        )
        response.raise_for_status()
        conversation = response.json()
        conversation_id = int(conversation["id"])
        try:
            timeout = httpx.Timeout(connect=10, read=case_timeout, write=30, pool=30)
            with client.stream(
                "POST",
                f"{api}/chat/{conversation_id}/stream",
                json={"content": case.prompt, "locale": "zh-CN"},
                timeout=timeout,
            ) as stream:
                stream_status = stream.status_code
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if line and line.startswith("data:"):
                        stream_items.append(_parse_sse_line(line))
        except Exception as exc:
            stream_error = f"{type(exc).__name__}: {exc}"
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
        f"reliability={score['reliability_score']} intelligence={score['intelligence_score']} "
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
        "provider_available": stream_status == 200 and stream_error is None,
        "runtime_metrics": runtime_metrics,
        "score": score,
    }


def _load_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def _initial_summary(
    *,
    run_id: str,
    commit: str,
    catalog_name: str,
    catalog_version: str,
    config: LLMConfig,
    started_at: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "commit": commit,
        "suite": catalog_name,
        "suite_version": catalog_version,
        "model": config.model,
        "provider_host": config.provider_host,
        "llm_config_source": config.source,
        "started_at": started_at,
        "provider_probe": {},
        "provider_availability": 0.0,
        "environment_unchanged": False,
        "results": [],
        "aggregate": aggregate_scores([], False),
    }


def _build_parser() -> argparse.ArgumentParser:
    catalog = load_catalog()
    parser = argparse.ArgumentParser(
        description="Run Praxis's local PostgreSQL DBA reliability/intelligence eval."
    )
    parser.add_argument("--case", choices=["all", *catalog.by_id()], default="all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--settings-db", type=Path)
    parser.add_argument("--postgres-image", default="postgres:16-alpine")
    parser.add_argument("--workload-repeats", type=int, default=8)
    parser.add_argument("--case-timeout", type=float, default=900)
    parser.add_argument("--case-delay", type=float, default=3)
    parser.add_argument("--list-cases", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute a full isolated eval run and return a shell exit code."""
    catalog = load_catalog()
    if args.list_cases:
        for case in catalog.cases:
            print(f"{case.case_id}\t{case.title}")
        return 0
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    if args.workload_repeats < 1:
        raise ValueError("--workload-repeats must be at least 1")
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required for the isolated PostgreSQL fixture")

    run_id = _run_id()
    output_dir = (args.output or ARTIFACT_ROOT / run_id).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC).isoformat()
    config = resolve_llm_config(args.settings_db)
    summary = _initial_summary(
        run_id=run_id,
        commit=_git_commit(),
        catalog_name=catalog.suite,
        catalog_version=catalog.version,
        config=config,
        started_at=started_at,
    )
    baseline = _load_baseline(args.baseline)
    print(
        f"[eval] run={run_id} commit={summary['commit']} model={config.model} "
        f"provider={config.provider_host} config={config.source}",
        flush=True,
    )
    probe = _probe_provider(config)
    summary["provider_probe"] = probe
    if not probe["ok"]:
        summary["startup_error"] = f"Provider probe failed: {probe.get('error', 'unknown')}"
        write_reports(output_dir, summary, baseline)
        print(f"[eval] provider unavailable; report={output_dir / 'report.md'}", flush=True)
        return 2

    pg_port = _free_port()
    backend_port = _free_port()
    container = f"praxis-eval-pg-{run_id.lower()}-{os.getpid()}"
    backend: ChildProcess | None = None
    fixture: ChildProcess | None = None
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    postgres_started = False
    results: list[dict[str, Any]] = []
    runtime_error: str | None = None
    try:
        print(f"[eval] starting isolated PostgreSQL on 127.0.0.1:{pg_port}...", flush=True)
        _start_postgres(container, pg_port, args.postgres_image)
        postgres_started = True
        _seed_database(container, args.workload_repeats)
        before = _database_snapshot(container)

        runtime_dir = output_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
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
        backend = _start_logged_process(
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
        _wait_for_http(f"http://127.0.0.1:{backend_port}/health", backend.process)
        api = f"http://127.0.0.1:{backend_port}/api/v1"
        datasource_id, _ = _create_runtime_resources(api, pg_port, runtime_dir / "data")

        fixture_log = output_dir / "fixture.log"
        fixture_env = os.environ.copy()
        fixture_env.update(
            {
                "PG_DBA_LAB_DSN": (
                    f"postgresql://{PG_USER}:{PG_PASSWORD}@127.0.0.1:{pg_port}/{PG_DATABASE}"
                ),
                "PYTHONUNBUFFERED": "1",
            }
        )
        fixture = _start_logged_process(
            [sys.executable, "-m", "evals.pg_dba.session_fixture"],
            fixture_log,
            env=fixture_env,
        )
        _wait_for_fixture(fixture_log, fixture.process)
        print(f"[eval] real Praxis backend ready on 127.0.0.1:{backend_port}", flush=True)

        selected = catalog.cases if args.case == "all" else (catalog.by_id()[args.case],)
        for case in selected:
            for attempt in range(1, args.repeat + 1):
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
                if args.case_delay > 0:
                    time.sleep(args.case_delay)
        after = _database_snapshot(container)
    except Exception as exc:
        runtime_error = f"{type(exc).__name__}: {exc}"
        print(f"[eval] runtime failure: {runtime_error}", flush=True)
    finally:
        if fixture is not None:
            fixture.stop()
        if backend is not None:
            backend.stop()
        if postgres_started:
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
    json_path, report_path = write_reports(output_dir, summary, baseline)
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


def main() -> None:
    parser = _build_parser()
    try:
        code = run(parser.parse_args())
    except (RuntimeError, ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"eval failed: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
