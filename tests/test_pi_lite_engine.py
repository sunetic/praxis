import asyncio
import json
from pathlib import Path

from app.services.pi_lite_engine import PiLiteEngine


def test_pi_lite_engine_requires_runtime_probe_before_finalize(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "main.py"
    target.write_text("print('old')\n", encoding="utf-8")

    calls = {"count": 0}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.py",
                                                "content": (
                                                    "def main(payload, context):\n"
                                                    "    return {'ok': True, 'datasource_id': context.get('datasource_id')}\n"
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已完成代码更新，可继续验证。",
                                    "diff_summary": "Updated main.py",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        if calls["count"] == 3:
            assert any(
                "function_runtime_probe" in str(item.get("content") or "")
                for item in messages
                if item.get("role") == "user"
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 4:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "探测通过，已完成更新。",
                                    "diff_summary": "Updated main.py",
                                    "tests_suggested": ["run probe"],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=6, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="update main.py",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )
    assert result.changed_files == ["main.py"]
    assert result.diff_summary == "Updated main.py"
    assert result.assistant_message == "探测通过，已完成更新。"
    assert target.read_text(encoding="utf-8").startswith("def main(payload, context)")


def test_pi_lite_engine_retries_after_probe_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "main.py"
    target.write_text("result = {'ok': True}\n", encoding="utf-8")

    calls = {"count": 0}
    probe_failure_seen = {"value": False}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if any('"ok": false' in str(item.get("content") or "") for item in messages if item.get("role") == "tool"):
            probe_failure_seen["value"] = True

        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.py",
                                                "content": (
                                                    "class Generated(FunctionBase):\n"
                                                    "    def run(self, payload, context):\n"
                                                    "        return {'ok': missing_name}\n"
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        if calls["count"] == 3:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-3",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.py",
                                                "content": (
                                                    "class Generated(FunctionBase):\n"
                                                    "    def run(self, payload, context):\n"
                                                    "        return {'ok': True}\n"
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        if calls["count"] == 4:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-4",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        if calls["count"] == 5:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已根据运行探测修复。",
                                    "diff_summary": "Fixed runtime error",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=10, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="build function",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )

    assert result.changed_files == ["main.py"]
    assert result.diff_summary == "Fixed runtime error"
    assert probe_failure_seen["value"] is True
    assert "missing_name" not in target.read_text(encoding="utf-8")


def test_pi_lite_engine_accepts_step0_plan_message_before_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "main.py"
    target.write_text("print('old')\n", encoding="utf-8")

    calls = {"count": 0}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Plan: inspect main.py, rewrite entry to main(payload, context), then verify.",
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            assert any(
                "Plan acknowledged." in str(item.get("content") or "")
                for item in messages
                if item.get("role") == "user"
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.py",
                                                "content": (
                                                    "def main(payload, context):\n"
                                                    "    return {'ok': True}\n"
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 3:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 4:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已按计划完成修复并通过验证。",
                                    "diff_summary": "Updated main.py",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=6, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="update main.py",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )

    assert calls["count"] == 4
    assert result.diff_summary == "Updated main.py"
    assert result.assistant_message == "已按计划完成修复并通过验证。"
    assert target.read_text(encoding="utf-8").startswith("def main(payload, context)")


def test_pi_lite_engine_runtime_probe_payload_includes_datasource_ids(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "main.py"
    target.write_text(
        (
            "class Generated(FunctionBase):\n"
            "    def run(self, payload, context):\n"
            "        ids = payload.get('datasource_ids')\n"
            "        if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):\n"
            "            raise ValueError('datasource_ids must be a list of integers')\n"
            "        return {'count': len(ids)}\n"
        ),
        encoding="utf-8",
    )

    calls = {"count": 0}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = messages
        _ = tools
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已完成校验。",
                                    "diff_summary": "No code change",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=4, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="validate",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )

    assert result.diff_summary == "No code change"
    assert calls["count"] == 2


def test_pi_lite_engine_runtime_probe_rejects_system_role_value(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    return db.query('select 1', role='system')\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is False
    assert result_type is None
    assert "Unsupported role: system" in str(error or "")


def test_pi_lite_engine_runtime_probe_supports_platform_list(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    items = platform.list('datasource', limit=5)\n"
            "    if not items:\n"
            "        raise ValueError('expected datasource fixtures in probe')\n"
            "    return {'count': len(items)}\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is True
    assert error is None
    assert result_type == "dict"


def test_pi_lite_engine_runtime_probe_rejects_by_id_positional_calling(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    result = db.query_by_id('select 1', 1)\n"
            "    return {'rows': result.get('rows', [])}\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is False
    assert result_type is None
    assert "keyword-only datasource_id" in str(error or "")


def test_pi_lite_engine_runtime_probe_rejects_swallowed_db_exception(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    try:\n"
            "        db.query('SHOW DATABASES', datasource=1)\n"
            "    except Exception:\n"
            "        return {'rows': []}\n"
            "    return {'ok': True}\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is False
    assert result_type is None
    assert "cannot be swallowed" in str(error or "")


def test_pi_lite_engine_runtime_probe_rejects_direct_iteration_of_db_query_result(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    rows = db.query('SHOW DATABASES', datasource=1)\n"
            "    names = [row[0] for row in rows]\n"
            "    return {'names': names}\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is False
    assert result_type is None
    assert "result.get('rows', [])" in str(error or "")


def test_pi_lite_engine_runtime_probe_rejects_row_index_access_on_mapping_rows(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(
        (
            "def main(payload, context):\n"
            "    query_result = db.query('SHOW DATABASES', datasource=1)\n"
            "    rows = query_result.get('rows', [])\n"
            "    names = [row[0] for row in rows]\n"
            "    return {'names': names}\n"
        ),
        encoding="utf-8",
    )

    engine = PiLiteEngine(max_steps=2)
    ok, error, result_type = engine._run_function_runtime_probe(
        workspace_dir=workspace,
        payload=engine._default_probe_payload(),
        context=engine._default_probe_context(),
    )

    assert ok is False
    assert result_type is None
    assert "KeyError: 0" in str(error or "")


def test_pi_lite_engine_get_runtime_contract_tool(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text("def main(payload, context):\n    return {'ok': True}\n", encoding="utf-8")

    calls = {"count": 0}
    contract_seen = {"value": False}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if any('"context_contract"' in str(item.get("content") or "") for item in messages if item.get("role") == "tool"):
            contract_seen["value"] = True

        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_function_runtime_contract",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 3:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已读取契约并通过探测。",
                                    "diff_summary": "No code change",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=6, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="validate",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )

    assert result.diff_summary == "No code change"
    assert contract_seen["value"] is True


def test_pi_lite_engine_probe_required_again_after_main_edit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "main.py"
    target.write_text("def main(payload, context):\n    return {'ok': True}\n", encoding="utf-8")

    calls = {"count": 0}
    reminder_seen = {"value": False}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if any(
            "Before final JSON, call `function_runtime_probe`" in str(item.get("content") or "")
            for item in messages
            if item.get("role") == "user"
        ):
            reminder_seen["value"] = True

        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "edit_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.py",
                                                "old_text": "return {'ok': True}",
                                                "new_text": "return {'ok': False}",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 3:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "完成。",
                                    "diff_summary": "Updated main.py",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        if calls["count"] == 4:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-3",
                                    "type": "function",
                                    "function": {
                                        "name": "function_runtime_probe",
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 5:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "已通过最终探测。",
                                    "diff_summary": "Updated main.py",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=8, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="update",
            workspace_dir=workspace,
            allowed_files=["main.py"],
        )
    )

    assert result.diff_summary == "Updated main.py"
    assert reminder_seen["value"] is True
    assert "'ok': False" in target.read_text(encoding="utf-8")


def test_pi_lite_engine_function_prompt_mentions_scheduler_history_guidance(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    engine = PiLiteEngine()

    prompt = engine._build_system_prompt(workspace, ["main.py"])

    assert "scheduler_history.list(...)" in prompt
    assert "scheduler_history.delete(...)" in prompt
    assert "retention_seconds" in prompt
    assert "dry_run=True" in prompt


def test_pi_lite_engine_requires_preview_sync_for_page_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.tsx").write_text("export default function Page(){return <main>a</main>}\n", encoding="utf-8")
    (workspace / "preview.html").write_text(
        "<!doctype html><html><body><main>a</main></body></html>\n",
        encoding="utf-8",
    )

    calls = {"count": 0}
    reminder_seen = {"value": False}

    async def fake_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
        _ = tools
        calls["count"] += 1
        if any(
            "did not update preview.html" in str(item.get("content") or "")
            for item in messages
            if item.get("role") == "user"
        ):
            reminder_seen["value"] = True

        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "main.tsx",
                                                "content": "export default function Page(){return <main>i</main>}\n",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "页面已更新。",
                                    "diff_summary": "updated",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        if calls["count"] == 3:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "preview.html",
                                                "content": "<!doctype html><html><body><main>i</main></body></html>\n",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        if calls["count"] == 4:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_message": "页面已更新。",
                                    "diff_summary": "updated",
                                    "tests_suggested": [],
                                    "risk_notes": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected extra retry")

    engine = PiLiteEngine(max_steps=8, chat_completion=fake_chat_completion)
    result = asyncio.run(
        engine.run(
            goal="update page",
            workspace_dir=workspace,
            allowed_files=["main.tsx", "preview.html"],
        )
    )

    assert reminder_seen["value"] is True
    assert result.changed_files == ["main.tsx", "preview.html"]
    assert "<main>i</main>" in (workspace / "preview.html").read_text(encoding="utf-8")


def test_pi_lite_engine_probe_repair_hint_for_common_failures():
    engine = PiLiteEngine(max_steps=2)

    signature_hint = engine._build_probe_repair_hint(
        "TypeError: main() takes 1 positional argument but 2 were given"
    )
    assert "main(payload, context)" in signature_hint

    context_hint = engine._build_probe_repair_hint("AttributeError: 'dict' object has no attribute 'get_db'")
    assert "context.get" in context_hint

    datasource_hint = engine._build_probe_repair_hint("RuntimeDatasourceAccessError: No default datasource bound")
    assert "explicit datasource_id" in datasource_hint

    rows_hint = engine._build_probe_repair_hint(
        "ValueError: db.query(...) returns a mapping; use result.get('rows', []) before iterating rows"
    )
    assert "result.get('rows', [])" in rows_hint

    keyerror_hint = engine._build_probe_repair_hint("KeyError: 0")
    assert "row.get('Database')" in keyerror_hint


def test_pi_lite_engine_probe_repair_hint_empty_for_empty_error():
    engine = PiLiteEngine(max_steps=2)
    assert engine._build_probe_repair_hint("") == ""
