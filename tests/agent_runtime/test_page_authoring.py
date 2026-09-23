import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel
from test_models import config
from test_runtime import Script, call, returns

from app.db.base import Base
from app.models.models import Agent, Page, PageRelease
from app.services.agent.application import RuntimeApplication
from app.services.agent.models import ModelFactory
from app.services.function.native_authoring import AuthoringError, FunctionAuthoringStore
from app.services.page.contracts import PageSource
from app.services.page.native_authoring import PageAuthoringStore
from app.services.page.validation import compile_source, validate_page


@pytest.fixture
def page_authoring(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    with store.sessions.begin() as db:
        page = Page(name="Page test", draft_payload={"files": {}, "bindings": {}})
        db.add(page)
        db.flush()
        page_id = page.id
    return PageAuthoringStore(store.sessions), page_id


def save(authoring, code="export default function Page(){ return <main>Hello</main> }"):
    store, page_id = authoring
    return store.write(
        page_id,
        expected_revision=store.read(page_id)["revision_hash"],
        source=PageSource(files={"main.tsx": code}),
        run_id=None,
    )


def report(authoring, revision, *, browser="passed", html="<html>compiled test fixture</html>"):
    store, page_id = authoring
    return store.record_validation(
        page_id,
        revision_id=revision["revision_id"],
        revision_hash=revision["revision_hash"],
        checks=[
            {
                "name": name,
                "status": browser if name == "browser_runtime" else "passed",
                "executed": name != "browser_runtime" or browser != "unavailable",
            }
            for name in (
                "source_compile",
                "function_bindings",
                "browser_runtime",
                "binding_runtime",
            )
        ],
        html=html,
        run_id=None,
    )


def test_page_revisions_invalidate_checks_and_cannot_publish_forged_or_stale_evidence(
    page_authoring,
):
    store, page_id = page_authoring
    first = save(page_authoring)
    assert store.read(page_id)["changed_files"] == ["main.tsx"]
    check = report(page_authoring, first)
    second = save(page_authoring)
    assert first["revision_hash"] == second["revision_hash"]
    assert first["revision_id"] != second["revision_id"]
    assert store.read(page_id)["validation"] is None
    assert store.read(page_id)["changed_files"] == []
    with pytest.raises(AuthoringError):
        store.publish(page_id, expected_revision=first["revision_hash"], validation_id=check["id"])
    with pytest.raises(AuthoringError):
        store.preview(page_id, revision_id=first["revision_id"])
    with pytest.raises(AuthoringError):
        report(page_authoring, first)
    third = save(page_authoring, "bad source is still savable")
    with pytest.raises(AuthoringError):
        store.write(
            page_id,
            expected_revision=second["revision_hash"],
            source=PageSource(files={}),
            run_id=None,
        )
    assert store.read(page_id)["revision_id"] == third["revision_id"]


@pytest.mark.parametrize("status", ["failed", "unavailable", "not_run"])
def test_unexecuted_or_failed_page_cannot_publish(page_authoring, status):
    store, page_id = page_authoring
    revision = save(page_authoring)
    check = report(page_authoring, revision, browser=status)
    with pytest.raises(AuthoringError, match="checks have not passed"):
        store.publish(
            page_id, expected_revision=revision["revision_hash"], validation_id=check["id"]
        )
    assert store.preview(page_id, revision_id=revision["revision_id"])["html"]


def test_archived_page_cannot_publish_previously_valid_revision(page_authoring):
    store, page_id = page_authoring
    revision = save(page_authoring)
    check = report(page_authoring, revision)
    with store.sessions.begin() as db:
        db.get(Page, page_id).status = "archived"
    with pytest.raises(AuthoringError, match="Archived Pages"):
        store.publish(
            page_id, expected_revision=revision["revision_hash"], validation_id=check["id"]
        )
    with store.sessions() as db:
        assert db.get(Page, page_id).status == "archived"
        assert db.query(PageRelease).count() == 0


def test_page_publication_is_exact_idempotent_and_survives_new_draft(page_authoring):
    store, page_id = page_authoring
    revision = save(page_authoring)
    check = report(page_authoring, revision)
    released = store.publish(
        page_id, expected_revision=revision["revision_hash"], validation_id=check["id"]
    )
    assert (
        store.publish(
            page_id, expected_revision=revision["revision_hash"], validation_id=check["id"]
        )["release_id"]
        == released["release_id"]
    )
    save(page_authoring, "invalid new source")
    assert store.read(page_id)["current_release_id"] == released["release_id"]
    assert store.read(page_id)["released_revision_id"] == revision["revision_id"]
    assert store.read(page_id)["changed_files"] == ["main.tsx"]
    with store.sessions() as db:
        releases = db.query(PageRelease).all()
        assert len(releases) == 1
        assert releases[0].artifact_payload["revision_id"] == revision["revision_id"]


@pytest.mark.parametrize(
    "path",
    [
        "../secret.ts",
        "/etc/data.json",
        "x/../../data.ts",
        "x\\file.ts",
        "./main.tsx",
        ".env",
        "package.json",
    ],
)
def test_page_workspace_rejects_path_escape_and_executable_build_configuration(path):
    with pytest.raises(ValidationError):
        PageSource(files={path: "text"})


@pytest.mark.parametrize(
    "code",
    [
        "export default function Page( { return <main /> }",
        "import secret from '/etc/config.json'; export default () => <main>{secret}</main>",
        "import secret from '../../data.json'; export default () => <main>{secret}</main>",
        "import fs from 'node:fs'; export default () => <main>{fs}</main>",
    ],
)
async def test_real_compiler_rejects_syntax_and_external_imports(code):
    check, html = await compile_source(PageSource(files={"main.tsx": code}))
    assert check["status"] == "failed", check
    assert check["executed"] is True
    assert html is None


async def test_actual_compiler_uses_one_source_including_local_css_and_escapes_script_end():
    source = PageSource(
        files={
            "main.tsx": "import './style.css'; import { title } from './title'; export default () => <main>{title}</main>",
            "title.ts": "export const title = '</script><script>bad</script>'",
            "style.css": "main { color: navy }",
        }
    )
    check, html = await compile_source(source)
    assert check["status"] == "passed", check
    assert "navy" in html
    assert html.count("</script>") == 1
    assert "connect-src 'none'" in html


async def test_failed_compilation_does_not_run_browser_or_claim_integration(
    page_authoring, monkeypatch
):
    from app.services.page import validation

    async def never(_):
        pytest.fail("Browser must not execute an invalid compilation")

    monkeypatch.setattr(validation, "browser_check", never)
    revision = save(page_authoring, "syntax error !")
    store, page_id = page_authoring
    check = await validate_page(
        store, page_id, expected_revision=revision["revision_hash"], run_id=None
    )
    assert check["checks"][0]["status"] == "failed"
    assert check["checks"][2]["status"] == "not_run"
    assert check["checks"][3]["status"] == "not_run"
    assert check["checks"][3]["executed"] is False
    assert check["checks"][3]["applicable"] is False
    with pytest.raises(AuthoringError):
        store.preview(page_id, revision_id=revision["revision_id"])


async def test_same_native_run_creates_and_edits_page_owned_function_without_broadening_scope(
    store, page_authoring
):
    author, page_id = page_authoring
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation(
        "page-run", "local", scene={"page_ids": [page_id], "datasource_ids": []}
    )
    created = {}

    async def next_step(messages, info):
        results = returns(messages)
        if not results:
            yield call(
                "page_create_function",
                json.dumps({"page_id": page_id, "name": "Required dependency"}),
                "create",
            )
        elif len(results) == 1:
            created.update(results[-1].content)
            yield call("function_read", json.dumps({"function_id": created["function_id"]}), "read")
        elif len(results) == 2:
            yield call(
                "function_write",
                json.dumps(
                    {
                        "function_id": created["function_id"],
                        "expected_revision": results[-1].content["revision_hash"],
                        "code": "def main(payload, context):\n    return payload\n",
                        "dependencies": {},
                    }
                ),
                "write",
            )
        else:
            yield "依赖草稿已保存，尚未发布。"

    runtime.service.model_factory = AsyncMock(return_value=FunctionModel(stream_function=next_step))
    await runtime.start()
    try:
        definition = runtime.resolve("page-run", "local", {})
        row = runtime.service.submit(
            "page-run", "local", "create-dependency", "创建依赖草稿", definition
        )
        async with asyncio.timeout(10):
            while runtime.store.get(row["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        row = runtime.store.get(row["id"], "local")
        assert row["status"] == "finished", row
        assert [item["status"] for item in row["tool_calls"]] == ["succeeded"] * 3
        dependency = FunctionAuthoringStore(store.sessions).read(created["function_id"])
        assert dependency["revision_id"] and dependency["current_release_id"] is None
        assert author.read(page_id)["owned_functions"][0]["id"] == created["function_id"]
        with store.sessions.begin() as db:
            agent = Agent(name="No tools", prompt="", tools=[], skills=[], status="active")
            db.add(agent)
            db.flush()
            agent_id = agent.id
        assert (
            runtime.resolve("page-run", "local", {"agent_id": agent_id}).tool_names == frozenset()
        )
    finally:
        await runtime.close()


async def test_native_page_write_persists_typed_source_arguments(store, page_authoring):
    author, page_id = page_authoring
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("page-write", "local", scene={"page_ids": [page_id]})
    arguments = {
        "page_id": page_id,
        "expected_revision": author.read(page_id)["revision_hash"],
        "source": {
            "files": {"main.tsx": "export default () => <main>Hello</main>"},
            "bindings": {},
        },
    }
    script = Script([call("page_write", json.dumps(arguments), "save")], ["草稿已保存。"])
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        run = runtime.service.submit(
            "page-write",
            "local",
            "write",
            "保存页面草稿",
            runtime.resolve("page-write", "local", {}),
        )
        async with asyncio.timeout(10):
            while runtime.store.get(run["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(run["id"], "local")
        assert final["status"] == "finished", final
        assert final["tool_calls"][0]["arguments"] == arguments
        assert author.read(page_id)["files"] == arguments["source"]["files"]
    finally:
        await runtime.close()


async def test_missing_page_file_preserves_revision_facts_for_model_recovery(store, page_authoring):
    author, page_id = page_authoring
    revision = author.read(page_id)
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("page-read-missing", "local", scene={"page_ids": [page_id]})
    script = Script(
        [call("page_read", json.dumps({"page_id": page_id, "path": "main.tsx"}), "missing")],
        ["文件不存在，工作区为空。"],
    )
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        run = runtime.service.submit(
            "page-read-missing",
            "local",
            "read",
            "查看页面",
            runtime.resolve("page-read-missing", "local", {}),
        )
        async with asyncio.timeout(10):
            while runtime.store.get(run["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(run["id"], "local")
        assert final["status"] == "finished", final
        result = final["tool_calls"][0]["result"]
        assert result["outcome"] == "failed"
        facts = json.loads(result["content"])
        assert facts["revision_hash"] == revision["revision_hash"]
        assert facts["files"] == [] and facts["revision_id"] is None
    finally:
        await runtime.close()


def test_binding_revision_id_cannot_be_confused_with_content_hash():
    with pytest.raises(ValidationError, match="pattern"):
        PageSource(files={}, bindings={"calculate": {"function_id": 1, "revision_id": "a" * 64}})


def test_binding_changes_are_metadata_not_a_fictitious_source_file(page_authoring):
    author, page_id = page_authoring
    revision = save(page_authoring)
    current = author.read(page_id)
    result = author.write(
        page_id,
        expected_revision=revision["revision_hash"],
        source=PageSource(
            files=current["files"], bindings={"calculate": {"function_id": 1, "release_id": 1}}
        ),
        run_id=None,
    )
    for value in (result, author.read(page_id)):
        assert value["changed_files"] == []
        assert value["bindings_changed"] is True


@pytest.mark.parametrize("operation", ["page_write", "page_validate", "page_publish"])
async def test_page_access_does_not_authorize_foreign_function_bindings(
    store, page_authoring, operation
):
    author, page_id = page_authoring
    source = PageSource(
        files={"main.tsx": "export default () => <main>Hello</main>"},
        bindings={"foreign": {"function_id": 999, "release_id": 10}},
    )
    # Direct user editing is broader than this run's explicit resource scope.
    revision = author.write(
        page_id, expected_revision=author.read(page_id)["revision_hash"], source=source, run_id=None
    )
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("page-denied", "local", scene={"page_ids": [page_id]})
    arguments = {"page_id": page_id, "expected_revision": revision["revision_hash"]}
    if operation == "page_write":
        arguments["source"] = source.model_dump(mode="json")
    if operation == "page_publish":
        arguments["validation_id"] = "not-authorized-to-inspect"
    script = Script([call(operation, json.dumps(arguments), "foreign")], ["没有这个依赖的授权。"])
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        run = runtime.service.submit(
            "page-denied", "local", "deny", "处理页面", runtime.resolve("page-denied", "local", {})
        )
        async with asyncio.timeout(10):
            while runtime.store.get(run["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(run["id"], "local")
        assert final["status"] == "finished", final
        assert final["tool_calls"][0]["status"] == "failed"
        assert "authorization denied" in final["tool_calls"][0]["result"]["content"]
        assert final["approvals"] == []
        assert author.read(page_id)["revision_id"] == revision["revision_id"]
        assert author.read(page_id)["validation"] is None
    finally:
        await runtime.close()


@pytest.mark.parametrize("change_before_approval", [False, True])
async def test_page_publication_requires_exact_approved_revision(
    store, page_authoring, change_before_approval
):
    author, page_id = page_authoring
    revision = save(page_authoring)
    check = report(page_authoring, revision)
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("page-publish", "local", scene={"page_ids": [page_id]})
    script = Script(
        [
            call(
                "page_publish",
                json.dumps(
                    {
                        "page_id": page_id,
                        "expected_revision": revision["revision_hash"],
                        "validation_id": check["id"],
                    }
                ),
                "publish",
            )
        ],
        ["发布请求已处理。"],
    )
    runtime.service.model_factory = script.factory

    async def settled(run_id, target=None):
        async with asyncio.timeout(10):
            while True:
                row = runtime.store.get(run_id, "local")
                if (
                    row["status"] == target
                    if target
                    else row["status"] not in {"queued", "running"}
                ):
                    return row
                await asyncio.sleep(0.01)

    await runtime.start()
    try:
        run = runtime.service.submit(
            "page-publish",
            "local",
            "publish",
            "发布当前版本",
            runtime.resolve("page-publish", "local", {}),
        )
        paused = await settled(run["id"])
        assert paused["status"] == "waiting_approval"
        assert author.read(page_id)["current_release_id"] is None
        if change_before_approval:
            save(page_authoring, "export default () => <main>Changed</main>")
        approval = paused["approvals"][0]
        for _ in range(2):
            runtime.service.approve(run["id"], "local", "publish", approval["fingerprint"], True)
        final = await settled(run["id"], "finished")
        assert len(final["tool_calls"]) == 1
        assert final["tool_calls"][0]["status"] == (
            "failed" if change_before_approval else "succeeded"
        )
        with store.sessions() as db:
            assert db.query(PageRelease).count() == (0 if change_before_approval else 1)
    finally:
        await runtime.close()


async def test_platform_page_publication_cannot_bypass_checks(page_authoring):
    from app.services.platform.object_tools import ObjectToolError, ObjectToolService

    author, page_id = page_authoring
    revision = save(page_authoring)
    check = report(page_authoring, revision, browser="unavailable")
    service = ObjectToolService(session_factory=author.sessions)
    for payload in (
        {"expected_revision": revision["revision_hash"], "validation_id": check["id"]},
        {"artifact_payload": {"html": "<p>unchecked</p>", "passed": True}},
    ):
        with pytest.raises(ObjectToolError):
            await service.operate(
                object_type="page", object_id=page_id, action="publish", payload=payload
            )
    assert author.read(page_id)["current_release_id"] is None


def test_absent_bindings_do_not_require_a_fictitious_runtime_check(page_authoring):
    author, page_id = page_authoring
    revision = save(page_authoring)
    check = author.record_validation(
        page_id,
        revision_id=revision["revision_id"],
        revision_hash=revision["revision_hash"],
        checks=[
            {"name": name, "status": "passed", "executed": True}
            for name in ("source_compile", "function_bindings", "browser_runtime")
        ]
        + [
            {"name": "binding_runtime", "status": "not_run", "executed": False, "applicable": False}
        ],
        html="<main>Compiled test fixture</main>",
        run_id=None,
    )
    assert author.publish(
        page_id, expected_revision=revision["revision_hash"], validation_id=check["id"]
    )["release_id"]


@pytest.mark.parametrize(
    "binding_kind", ["draft", "foreign_release", "unchecked_release", "checked_release"]
)
def test_binding_checks_resolve_actual_function_and_validation(page_authoring, binding_kind):
    from app.models.models import Function, FunctionRelease
    from app.services.function.native_authoring import static_checks

    author, page_id = page_authoring
    created = author.create_function(page_id, name="Dependency", description="")
    function_id = created["function_id"]
    functions = FunctionAuthoringStore(author.sessions)
    code = "def main(payload, context):\n    return payload\n"
    revision = functions.write(
        function_id,
        expected_revision=functions.read(function_id)["revision_hash"],
        code=code,
        dependencies={},
        run_id=None,
    )
    binding = {"function_id": function_id}
    if binding_kind == "draft":
        binding["revision_id"] = revision["revision_id"]
    elif binding_kind == "checked_release":
        # Synthetic evidence isolates dependency/version checks, not sandbox success.
        validation = functions.record_validation(
            function_id,
            revision_id=revision["revision_id"],
            revision_hash=revision["revision_hash"],
            run_id=None,
            checks=[
                *static_checks(code),
                {"name": "controlled_runtime", "status": "passed", "executed": True},
            ],
        )
        binding["release_id"] = functions.publish(
            function_id, expected_revision=revision["revision_hash"], validation_id=validation["id"]
        )["release_id"]
        # A later draft must not change the already bound immutable release.
        functions.write(
            function_id,
            expected_revision=revision["revision_hash"],
            code="invalid",
            dependencies={},
            run_id=None,
        )
    else:
        with author.sessions.begin() as db:
            if binding_kind == "foreign_release":
                foreign = Function(name="Foreign", draft_code="")
                db.add(foreign)
                db.flush()
                release_function_id = foreign.id
            else:
                release_function_id = function_id
            release = FunctionRelease(
                function_id=release_function_id,
                version=1,
                code_snapshot=code,
                dependency_manifest={},
                release_metadata={},
            )
            db.add(release)
            db.flush()
            binding["release_id"] = release.id
    check = author.inspect_bindings({"calculate": binding})
    assert check["executed"] is True
    assert check["status"] == ("passed" if binding_kind == "checked_release" else "failed")
