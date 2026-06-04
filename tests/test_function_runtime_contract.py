import json

from app.services.function.runtime_contract import (
    get_function_runtime_contract,
    get_function_runtime_contract_block,
    get_function_runtime_contract_json,
)


def test_function_runtime_contract_is_machine_readable():
    contract = get_function_runtime_contract()
    assert isinstance(contract, dict)
    assert contract.get("contract_version") == "function-runtime-v3"
    context_contract = contract.get("context_contract") or {}
    assert context_contract.get("type") == "dict"
    assert context_contract.get("access_style") == "dict_get_only"


def test_function_runtime_contract_block_contains_json():
    block = get_function_runtime_contract_block()
    assert block.startswith("Fixed Runtime Contract (JSON):")
    payload = block.split(":\n", 1)[1]
    parsed = json.loads(payload)
    assert parsed.get("entrypoints", {}).get("main", {}).get("signature") == "main(payload, context)"
    assert "get_function_runtime_contract" not in payload


def test_function_runtime_contract_json_is_stable_shape():
    parsed = json.loads(get_function_runtime_contract_json())
    assert parsed.get("db_api", {}).get("main_helper") == "db"
    db_methods = parsed.get("db_api", {}).get("methods") or []
    assert "query_by_id(sql, *, datasource_id=..., params=?)" in db_methods
    assert "explain_by_id(sql, *, datasource_id=...)" in db_methods
    assert "get_conn_by_id(datasource_id)" in db_methods
    assert "get_session_by_id(datasource_id)" in db_methods
    datasource_policy = parsed.get("db_api", {}).get("datasource_policy") or {}
    assert "platform.list('datasource')" in str(datasource_policy.get("metadata_discovery") or "")
    assert "datasource_id only" in str(datasource_policy.get("strict_calling") or "")
    result_shape = parsed.get("db_api", {}).get("result_shape") or {}
    assert result_shape.get("query_or_query_by_id", {}).get("type") == "mapping"
    assert "result.get('rows', [])" in str(result_shape.get("usage_note") or "")
    platform_api = parsed.get("platform_api") or {}
    assert platform_api.get("main_helper") == "platform"
    assert "list(object_type, filters=?, limit=?)" in (platform_api.get("methods") or [])
    assert "context['db']" in (parsed.get("context_contract", {}).get("forbidden_patterns") or [])
