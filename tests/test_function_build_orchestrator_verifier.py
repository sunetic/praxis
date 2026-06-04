from __future__ import annotations

from typing import Any

from app.models import models
from app.services.function.build_orchestrator import FunctionVerifier
from app.services.function.runtime import FunctionRuntimeResult


def _make_function(*, code: str) -> models.Function:
    return models.Function(
        name="runtime-verify-test",
        draft_code=code,
        draft_dependencies={},
    )


def _runtime_result(*, status: str, error_message: str = "", output: Any = None) -> FunctionRuntimeResult:
    return FunctionRuntimeResult(
        run_id="test-run",
        status=status,
        output=output,
        error_class=None,
        error_code=None,
        error_message=error_message,
        duration_ms=1,
    )


class _FakeRuntimeService:
    def __init__(self, *, results: list[FunctionRuntimeResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> FunctionRuntimeResult:
        function = args[0] if args else None
        payload = args[1] if len(args) > 1 else None
        self.calls.append({
            "function": function,
            "payload": payload,
            "kwargs": kwargs,
        })
        if self._results:
            return self._results.pop(0)
        return _runtime_result(status="success", output={"ok": True})


def test_function_verifier_runtime_environment_unavailable_is_soft_degraded() -> None:
    verifier = FunctionVerifier(
        runtime_service_factory=lambda: _FakeRuntimeService(
            results=[
                _runtime_result(
                    status="failed",
                    error_message=(
                        "RuntimeError: An attempt has been made to start a new process before the current process "
                        "has finished its bootstrapping phase."
                    ),
                )
            ]
        )
    )
    function = _make_function(code="def main(payload, context):\n    return {'ok': True}\n")

    report = verifier.verify(function=function, db=object())

    runtime_report = report.get("runtime_verification") or {}
    assert report.get("passed") is True
    assert runtime_report.get("enforced") is False
    assert runtime_report.get("passed") is True
    assert runtime_report.get("reason") == "runtime_environment_unavailable"


def test_function_verifier_runtime_failure_blocks_when_enforced() -> None:
    verifier = FunctionVerifier(
        runtime_service_factory=lambda: _FakeRuntimeService(
            results=[
                _runtime_result(status="failed", error_message="RuntimeError: business failure"),
            ]
        )
    )
    function = _make_function(code="def main(payload, context):\n    return {'ok': True}\n")

    report = verifier.verify(function=function, db=object())

    runtime_report = report.get("runtime_verification") or {}
    assert runtime_report.get("enforced") is True
    assert runtime_report.get("passed") is False
    assert report.get("passed") is False


def test_function_verifier_runtime_samples_pass_when_success_and_failure_expectations_met() -> None:
    verifier = FunctionVerifier(
        runtime_service_factory=lambda: _FakeRuntimeService(
            results=[
                _runtime_result(status="success", output={"ok": True}),
                _runtime_result(status="failed", error_message="KeyError: target_schedule_id"),
            ]
        )
    )
    function = _make_function(
        code=(
            "def main(payload, context):\n"
            "    return {'target': int(payload['target_schedule_id']), 'dry_run': bool(payload.get('dry_run', False))}\n"
        )
    )

    report = verifier.verify(function=function, db=object())

    runtime_report = report.get("runtime_verification") or {}
    assert runtime_report.get("enforced") is True
    assert runtime_report.get("passed") is True
    assert report.get("passed") is True


def test_function_verifier_infers_numeric_payload_samples_from_code_casts() -> None:
    runtime = _FakeRuntimeService(
        results=[
            _runtime_result(status="success", output={"ok": True}),
            _runtime_result(status="failed", error_message="KeyError: datasource_id"),
        ]
    )
    verifier = FunctionVerifier(runtime_service_factory=lambda: runtime)
    function = _make_function(
        code=(
            "def _safe_int(value, default):\n"
            "    try:\n"
            "        return int(value)\n"
            "    except (TypeError, ValueError):\n"
            "        return default\n\n"
            "def main(payload, context):\n"
            "    lookback_days = _safe_int(payload.get('lookback_days'), 7)\n"
            "    stale_days = int(payload['stale_days'])\n"
            "    datasource_id = int(payload['datasource_id'])\n"
            "    return {'ok': True, 'lookback_days': lookback_days, 'stale_days': stale_days, 'datasource_id': datasource_id}\n"
        )
    )

    report = verifier.verify(function=function, db=object(), schema_probe={"datasource_id": 23})

    runtime_report = report.get("runtime_verification") or {}
    assert runtime_report.get("passed") is True
    assert len(runtime.calls) >= 1
    success_payload = runtime.calls[0]["payload"]
    assert success_payload["datasource_id"] == 23
    assert success_payload["lookback_days"] == 1
    assert success_payload["stale_days"] == 1
