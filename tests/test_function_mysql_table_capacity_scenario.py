from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chat import ChatService
from app.services.function.chat_agent import FunctionChatAgent

MYSQL_TABLE_CAPACITY_PROMPT = """
构建一个单一目标的 MySQL Function：查询指定数据源中占用空间最大的表。

输入：
- datasource_id：必填，正整数。
- schema：可选；传入时只查询该 schema，未传时排除 MySQL 系统库。
- limit：可选，默认 20，范围 1 到 100。

输出：
- ok、count、tables。
- tables 每项包含 schema、table、engine、estimated_rows、data_mb、index_mb、total_mb。

必须使用 information_schema.tables 和参数化 SQL，只读执行；空结果是合法结果；数据库异常必须带业务上下文重新抛出，不能返回伪造数据。
""".strip()


MYSQL_TABLE_CAPACITY_CODE = '''def _positive_int(value, *, default=None):
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def main(payload, context):
    datasource_id = _positive_int(
        payload.get("datasource_id"),
        default=_positive_int(context.get("datasource_id")),
    )
    if datasource_id is None:
        return {"ok": False, "error": "datasource_id must be a positive integer"}

    limit = _positive_int(payload.get("limit"), default=20)
    if limit is None or limit > 100:
        return {"ok": False, "error": "limit must be between 1 and 100"}

    schema = str(payload.get("schema") or "").strip()
    sql = """
        SELECT
            table_schema,
            table_name,
            engine,
            table_rows AS estimated_rows,
            ROUND(data_length / 1024 / 1024, 2) AS data_mb,
            ROUND(index_length / 1024 / 1024, 2) AS index_mb,
            ROUND((data_length + index_length) / 1024 / 1024, 2) AS total_mb
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
    """
    params = []
    if schema:
        sql += " AND table_schema = %s"
        params.append(schema)
    else:
        sql += " AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')"
    sql += " ORDER BY (data_length + index_length) DESC LIMIT %s"
    params.append(limit)

    try:
        query_result = db.query_by_id(
            sql=sql,
            datasource_id=datasource_id,
            params=params,
        )
    except Exception as exc:
        raise RuntimeError("Failed to query MySQL table capacity") from exc

    tables = []
    for row in query_result.get("rows", []):
        tables.append(
            {
                "schema": row.get("table_schema"),
                "table": row.get("table_name"),
                "engine": row.get("engine"),
                "estimated_rows": row.get("estimated_rows"),
                "data_mb": row.get("data_mb"),
                "index_mb": row.get("index_mb"),
                "total_mb": row.get("total_mb"),
            }
        )
    return {"ok": True, "count": len(tables), "tables": tables}
'''


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _text_response(content: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}


class _ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, stream=False, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "stream": stream})
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        yield self.responses.pop(0)


class _RecordingDB:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query_by_id(
        self,
        *,
        sql: str,
        datasource_id: int,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"sql": sql, "datasource_id": datasource_id, "params": list(params or [])}
        )
        if self.error is not None:
            raise self.error
        return {"rows": self.rows, "row_count": len(self.rows)}


def _load_main(code: str, database: _RecordingDB):
    namespace: dict[str, Any] = {"db": database}
    exec(code, namespace, namespace)
    return namespace["main"]


@pytest.mark.asyncio
async def test_mysql_table_capacity_function_build_and_business_acceptance(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    llm = _ScriptedLLM(
        [
            _tool_response("read", "function_source_read", {}),
            _tool_response("contract", "get_function_runtime_contract", {}),
            _tool_response(
                "write",
                "function_source_replace",
                {"code": MYSQL_TABLE_CAPACITY_CODE},
            ),
            _tool_response(
                "probe",
                "function_runtime_probe",
                {"payload": {"datasource_id": 7, "schema": "sales", "limit": 10}},
            ),
            _tool_response(
                "finish",
                "function_build_finish",
                {
                    "outcome": "completed",
                    "assistant_message": "MySQL 表容量排行 Function 已完成并通过运行验证。",
                    "diff_summary": "Added parameterized information_schema table capacity query",
                    "tests_suggested": [
                        "指定 schema 查询容量排行",
                        "不指定 schema 时确认系统库被排除",
                        "验证数据库异常不会被吞掉",
                    ],
                    "risk_notes": ["table_rows is an estimated row count for InnoDB"],
                },
            ),
            _text_response("MySQL 表容量排行 Function 已完成并通过运行验证。"),
        ]
    )
    agent = FunctionChatAgent(chat_service=ChatService(llm=llm))

    build_result = await agent.run_coding_task(
        goal=MYSQL_TABLE_CAPACITY_PROMPT,
        workspace_dir=tmp_path,
        purpose="implement",
        build_context={"datasource_id": 7, "database_type": "mysql"},
        max_iterations=10,
    )

    assert build_result.result_status == "completed"
    assert build_result.changed_files == ["main.py"]
    assert "information_schema" in build_result.diff_summary
    assert all(call["stream"] is True for call in llm.calls)

    generated_code = (tmp_path / "main.py").read_text(encoding="utf-8")
    rows = [
        {
            "table_schema": "sales",
            "table_name": "orders",
            "engine": "InnoDB",
            "estimated_rows": 120000,
            "data_mb": 96.5,
            "index_mb": 31.25,
            "total_mb": 127.75,
        }
    ]
    database = _RecordingDB(rows=rows)
    main = _load_main(generated_code, database)

    output = main(
        {"datasource_id": 23, "schema": "sales", "limit": 5},
        {"datasource_id": None},
    )

    assert output == {
        "ok": True,
        "count": 1,
        "tables": [
            {
                "schema": "sales",
                "table": "orders",
                "engine": "InnoDB",
                "estimated_rows": 120000,
                "data_mb": 96.5,
                "index_mb": 31.25,
                "total_mb": 127.75,
            }
        ],
    }
    assert len(database.calls) == 1
    assert database.calls[0]["datasource_id"] == 23
    assert database.calls[0]["params"] == ["sales", 5]
    assert "table_schema = %s" in database.calls[0]["sql"]
    assert "sales" not in database.calls[0]["sql"]

    empty_database = _RecordingDB()
    empty_output = _load_main(generated_code, empty_database)(
        {"datasource_id": 23},
        {},
    )
    assert empty_output == {"ok": True, "count": 0, "tables": []}
    assert "table_schema NOT IN" in empty_database.calls[0]["sql"]
    assert empty_database.calls[0]["params"] == [20]

    invalid_output = main({"datasource_id": 23, "limit": 101}, {})
    assert invalid_output == {"ok": False, "error": "limit must be between 1 and 100"}
    assert len(database.calls) == 1

    failing_main = _load_main(generated_code, _RecordingDB(error=TimeoutError("timeout")))
    with pytest.raises(RuntimeError, match="MySQL table capacity") as exc_info:
        failing_main({"datasource_id": 23}, {})
    assert isinstance(exc_info.value.__cause__, TimeoutError)
