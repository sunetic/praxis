import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.functions import FunctionInvoke, cancel_function_run, invoke_function
from app.db.database import Base
from app.models import models
from app.services.function.runtime import FunctionRuntimeService


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def runtime_app(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'function-api.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    runtime = FunctionRuntimeService(session_factory=sessions)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                agent_runtime=SimpleNamespace(sessions=sessions, functions=runtime)
            )
        )
    )
    try:
        yield sessions, runtime, request
    finally:
        engine.dispose()


def seed_function(sessions, code: str) -> int:
    with sessions() as db:
        function = models.Function(name="api-function", slug=uuid4().hex, status="released")
        db.add(function)
        db.flush()
        release = models.FunctionRelease(
            function_id=function.id,
            version=1,
            code_snapshot=code,
            dependency_manifest={},
        )
        db.add(release)
        db.flush()
        function.current_release_id = release.id
        db.commit()
        return int(function.id)


@pytest.mark.anyio
async def test_invoke_api_uses_application_runtime_and_persists_full_result(runtime_app):
    sessions, runtime, request = runtime_app
    function_id = seed_function(
        sessions,
        "def main(payload, context):\n    return {'doubled': payload['value'] * 2}\n",
    )
    run_id = uuid4().hex
    try:
        response = await invoke_function(
            function_id,
            FunctionInvoke(run_id=run_id, payload={"value": 4}),
            request,
        )
    finally:
        await runtime.close()

    assert response["run_id"] == run_id
    assert response["output"] == {"doubled": 8}
    with sessions() as db:
        row = db.query(models.FunctionRun).filter_by(run_id=run_id).one()
        assert row.output_payload == {"doubled": 8}
        assert row.error_code is None


@pytest.mark.anyio
async def test_cancel_api_stops_application_owned_invocation(runtime_app):
    sessions, runtime, request = runtime_app
    function_id = seed_function(
        sessions,
        "import time\ntime.sleep(5)\nresult = {'finished': True}\n",
    )
    run_id = uuid4().hex
    invocation = asyncio.create_task(
        invoke_function(
            function_id,
            FunctionInvoke(run_id=run_id, payload={}, timeout_seconds=10),
            request,
        )
    )
    try:
        async with asyncio.timeout(2):
            while runtime.get_result(run_id) is None:
                await asyncio.sleep(0.01)
        cancelled = await cancel_function_run(function_id, run_id, request)
        original = await invocation
    finally:
        await runtime.close()

    assert cancelled["status"] == "cancelled"
    assert cancelled["error_code"] == "cancelled"
    assert original["status"] == "cancelled"
