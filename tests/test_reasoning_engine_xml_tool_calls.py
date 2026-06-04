"""Tests for XML-style tool call extraction in reasoning engine."""

import json

from app.services.agent.reasoning_engine import _extract_xml_tool_calls


def test_extract_single_xml_tool_call():
    text = (
        "是否需要我帮您启用慢查询日志？\n\n"
        "<function=execute_sql> "
        "<parameter=sql> SELECT 1; </parameter> "
        "<parameter=intent> 测试查询 </parameter> "
        "</function> </tool_call>"
    )
    cleaned, calls = _extract_xml_tool_calls(text)
    assert "是否需要我帮您启用慢查询日志？" in cleaned
    assert "<function=" not in cleaned
    assert "</tool_call>" not in cleaned
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "execute_sql"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["sql"] == "SELECT 1;"
    assert args["intent"] == "测试查询"


def test_extract_multiple_xml_tool_calls():
    text = (
        "让我查询一下。\n\n"
        "<function=execute_sql> <parameter=sql> SELECT 1; </parameter> "
        "<parameter=intent> first </parameter> </function> </tool_call>\n\n"
        "<function=execute_sql> <parameter=sql> SHOW PROCESSLIST; </parameter> "
        "<parameter=intent> second </parameter> </function> </tool_call>"
    )
    cleaned, calls = _extract_xml_tool_calls(text)
    assert cleaned == "让我查询一下。"
    assert len(calls) == 2
    args0 = json.loads(calls[0]["function"]["arguments"])
    args1 = json.loads(calls[1]["function"]["arguments"])
    assert args0["sql"] == "SELECT 1;"
    assert args1["sql"] == "SHOW PROCESSLIST;"


def test_no_xml_tool_calls_returns_unchanged():
    text = "这是一段普通文本，没有工具调用。"
    cleaned, calls = _extract_xml_tool_calls(text)
    assert cleaned == text
    assert calls == []


def test_extract_xml_tool_call_without_trailing_tool_call_tag():
    text = "<function=explain_sql> <parameter=sql> SELECT 1; </parameter> </function>"
    cleaned, calls = _extract_xml_tool_calls(text)
    assert cleaned == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "explain_sql"


def test_multiline_sql_in_xml_tool_call():
    text = (
        "我来分析一下：\n\n"
        "<function=execute_sql> <parameter=sql>\n"
        "SELECT SCHEMA_NAME as database_name,\n"
        "       DIGEST_TEXT as sql_template\n"
        "FROM performance_schema.events_statements_summary_by_digest\n"
        "WHERE LAST_SEEN >= NOW() - INTERVAL 24 HOUR\n"
        "ORDER BY AVG_TIMER_WAIT DESC\n"
        "LIMIT 20;\n"
        "</parameter> <parameter=intent> 查询最近24小时的SQL执行统计 </parameter> "
        "</function> </tool_call>"
    )
    cleaned, calls = _extract_xml_tool_calls(text)
    assert cleaned == "我来分析一下："
    assert len(calls) == 1
    args = json.loads(calls[0]["function"]["arguments"])
    assert "performance_schema" in args["sql"]
    assert "LIMIT 20" in args["sql"]


def test_generated_ids_are_unique():
    text = (
        "<function=execute_sql> <parameter=sql> SELECT 1; </parameter> </function>\n"
        "<function=execute_sql> <parameter=sql> SELECT 2; </parameter> </function>"
    )
    _, calls = _extract_xml_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["id"] != calls[1]["id"]
    assert all(c["id"].startswith("xmltc_") for c in calls)
