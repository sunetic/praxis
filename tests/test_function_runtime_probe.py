from __future__ import annotations

from pathlib import Path

import pytest

from app.services.function.runtime_probe import FunctionRuntimeProbe


def _run_probe(tmp_path: Path, source: str) -> tuple[bool, str | None, str | None]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(source, encoding="utf-8")
    probe = FunctionRuntimeProbe()
    return probe.run(
        workspace_dir=workspace,
        payload=probe.default_payload(),
        context=probe.default_context(),
    )


@pytest.mark.parametrize(
    ("source", "expected_error"),
    [
        (
            "def main(payload, context):\n    return db.query('select 1', role='system')\n",
            "Unsupported role: system",
        ),
        (
            "def main(payload, context):\n"
            "    result = db.query_by_id('select 1', 1)\n"
            "    return {'rows': result.get('rows', [])}\n",
            "keyword-only datasource_id",
        ),
        (
            "def main(payload, context):\n"
            "    try:\n"
            "        db.query('SHOW DATABASES', datasource=1)\n"
            "    except Exception:\n"
            "        return {'rows': []}\n"
            "    return {'ok': True}\n",
            "cannot be swallowed",
        ),
        (
            "def main(payload, context):\n"
            "    rows = db.query('SHOW DATABASES', datasource=1)\n"
            "    return {'names': [row[0] for row in rows]}\n",
            "result.get('rows', [])",
        ),
        (
            "def main(payload, context):\n"
            "    result = db.query('SHOW DATABASES', datasource=1)\n"
            "    return {'names': [row[0] for row in result.get('rows', [])]}\n",
            "KeyError: 0",
        ),
    ],
)
def test_function_runtime_probe_rejects_invalid_runtime_usage(
    tmp_path: Path, source: str, expected_error: str
) -> None:
    ok, error, result_type = _run_probe(tmp_path, source)

    assert ok is False
    assert result_type is None
    assert expected_error in str(error or "")


def test_function_runtime_probe_supports_platform_and_database_contract(tmp_path: Path) -> None:
    ok, error, result_type = _run_probe(
        tmp_path,
        "def main(payload, context):\n"
        "    items = platform.list('datasource', limit=5)\n"
        "    result = db.query_by_id(sql='select 1', datasource_id=items[0]['id'])\n"
        "    return {'datasource_count': len(items), 'rows': result.get('rows', [])}\n",
    )

    assert ok is True
    assert error is None
    assert result_type == "dict"


def test_function_runtime_probe_repair_hint_covers_common_contract_failures() -> None:
    hint = FunctionRuntimeProbe.repair_hint("Runtime contract violation")

    assert "main(payload, context)" in hint
    assert "context.get" in hint
    assert "explicit datasource_id" in hint
    assert "result.get('rows', [])" in hint
    assert "row.get('Database')" in hint
    assert FunctionRuntimeProbe.repair_hint("") == ""
