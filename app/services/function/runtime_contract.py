from __future__ import annotations

import json
from typing import Any

_FUNCTION_RUNTIME_CONTRACT: dict[str, Any] = {
    "contract_version": "function-runtime-v3",
    "entrypoints": {
        "main": {
            "signature": "main(payload, context)",
            "required_positional_args": 2,
        },
        "class_run": {
            "base_class": "FunctionBase",
            "signature": "run(self, payload, context)",
        },
    },
    "context_contract": {
        "type": "dict",
        "allowed_keys": {
            "datasource_id": "int|null",
            "scope": "dict",
            "trace_id": "str",
            "execution_mode": "plan|apply",
        },
        "access_style": "dict_get_only",
        "forbidden_patterns": [
            "context.get_db(...)",
            "context.db",
            "context.session",
            "context['db']",
        ],
    },
    "db_api": {
        "main_helper": "db",
        "class_helper": "self.db",
        "methods": [
            "query(sql, datasource=?, params=?)",
            "explain(sql, datasource=?)",
            "query_by_id(sql, *, datasource_id=..., params=?)",
            "explain_by_id(sql, *, datasource_id=...)",
            "get_conn_by_id(datasource_id)",
            "get_session_by_id(datasource_id)",
        ],
        "datasource_policy": {
            "metadata_discovery": "use platform.list('datasource')",
            "explicit_id_for_business_query": "when default datasource is absent, pass datasource_id explicitly for user-tenant SQL",
            "strict_calling": "get_conn_by_id/get_session_by_id accept datasource_id only; do not pass role",
        },
        "result_shape": {
            "query_or_query_by_id": {
                "type": "mapping",
                "required_keys": ["rows"],
                "rows_type": "list[dict]",
            },
            "usage_note": "Always iterate `result.get('rows', [])`; never iterate db.query(...) return object directly.",
        },
        "role_enum": ["user", "sys"],
        "schemas": {
            "query": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "sql": {"type": "string", "min_length": 1},
                    "datasource": {
                        "one_of": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "min_length": 1},
                            {"type": "null"},
                        ],
                    },
                    "role": {"type": "string", "enum": ["user", "sys"], "default": "user"},
                    "params": {"type": "array", "items": {"type": "any"}},
                },
                "constraints": {"required": ["sql"]},
            },
            "explain": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "sql": {"type": "string", "min_length": 1},
                    "datasource": {
                        "one_of": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "min_length": 1},
                            {"type": "null"},
                        ],
                    },
                    "role": {"type": "string", "enum": ["user", "sys"], "default": "user"},
                },
                "constraints": {"required": ["sql"]},
            },
            "query_by_id": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "sql": {"type": "string", "min_length": 1},
                    "datasource_id": {"type": "integer", "minimum": 1},
                    "params": {"type": "array", "items": {"type": "any"}},
                },
                "constraints": {"required": ["sql", "datasource_id"]},
            },
            "explain_by_id": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "sql": {"type": "string", "min_length": 1},
                    "datasource_id": {"type": "integer", "minimum": 1},
                },
                "constraints": {"required": ["sql", "datasource_id"]},
            },
            "get_conn_by_id": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "datasource_id": {"type": "integer", "minimum": 1},
                },
                "constraints": {"required": ["datasource_id"]},
            },
            "get_session_by_id": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "datasource_id": {"type": "integer", "minimum": 1},
                },
                "constraints": {"required": ["datasource_id"]},
            },
        },
    },
    "platform_api": {
        "main_helper": "platform",
        "class_helper": "self.platform",
        "object_types": [
            "page",
            "function",
            "scheduler",
            "datasource",
            "scheduler_history",
            "channel",
        ],
        "methods": [
            "list(object_type, filters=?, limit=?)",
            "get(object_type, object_id)",
            "crud(object_type, action, object_id=?, payload=?)",
            "operate(object_type, action, object_id, payload=?)",
        ],
        "plan_mode_policy": {
            "crud_write_forbidden": ["create", "update", "delete"],
            "operate_forbidden": True,
        },
        "action_enums": {
            "crud": ["create", "read", "update", "delete", "list"],
            "operate": [
                "preview",
                "publish",
                "archive",
                "rollback",
                "release",
                "strategy",
                "verify",
                "invoke",
                "pause",
                "resume",
                "run-now",
                "list-runs",
                "send",
            ],
        },
        "schemas": {
            "list": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": [
                            "page",
                            "function",
                            "scheduler",
                            "datasource",
                            "scheduler_history",
                            "channel",
                        ],
                    },
                    "filters": {"type": "object"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                },
                "constraints": {"required": ["object_type"]},
            },
            "get": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": [
                            "page",
                            "function",
                            "scheduler",
                            "datasource",
                            "scheduler_history",
                            "channel",
                        ],
                    },
                    "object_id": {"type": "integer", "minimum": 1},
                },
                "constraints": {"required": ["object_type", "object_id"]},
            },
            "crud": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": [
                            "page",
                            "function",
                            "scheduler",
                            "datasource",
                            "scheduler_history",
                            "channel",
                        ],
                    },
                    "action": {
                        "type": "string",
                        "enum": ["create", "read", "update", "delete", "list"],
                    },
                    "object_id": {"type": "integer", "minimum": 1},
                    "payload": {"type": "object"},
                },
                "constraints": {"required": ["object_type", "action"]},
            },
            "operate": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": ["page", "function", "scheduler", "datasource", "channel"],
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "preview",
                            "publish",
                            "archive",
                            "rollback",
                            "release",
                            "strategy",
                            "verify",
                            "invoke",
                            "pause",
                            "resume",
                            "run-now",
                            "list-runs",
                            "send",
                        ],
                    },
                    "object_id": {"type": "integer", "minimum": 1},
                    "payload": {"type": "object"},
                },
                "constraints": {"required": ["object_type", "action", "object_id"]},
            },
        },
        "list_filter_schemas": {
            "page": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["draft", "previewing", "published", "archived"],
                    },
                },
            },
            "function": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["draft", "released"]},
                },
            },
            "scheduler": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["active", "paused"]},
                    "target_type": {"type": "string", "enum": ["function", "agent"]},
                },
            },
            "datasource": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["active", "inactive"]},
                    "tenant_role": {"type": "string", "enum": ["user", "sys"]},
                },
            },
            "scheduler_history": {"$ref": "scheduler_history_api.schemas.where"},
            "channel": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["dingtalk", "feishu", "wechat", "slack", "telegram"],
                    },
                    "status": {"type": "string", "enum": ["active", "inactive"]},
                },
            },
        },
        "crud_payload_schemas": {
            "page": {
                "create": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_payload": {"one_of": [{"type": "object"}, {"type": "null"}]},
                    },
                    "constraints": {"required": ["name"]},
                },
                "update": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_payload": {"one_of": [{"type": "object"}, {"type": "null"}]},
                    },
                },
            },
            "function": {
                "create": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_code": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_dependencies": {"one_of": [{"type": "object"}, {"type": "null"}]},
                    },
                    "constraints": {"required": ["name"]},
                },
                "update": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_code": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "draft_dependencies": {"one_of": [{"type": "object"}, {"type": "null"}]},
                    },
                },
            },
            "scheduler": {
                "create": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "target_type": {
                            "type": "string",
                            "enum": ["function", "agent"],
                            "default": "function",
                        },
                        "target_id": {"type": "integer", "minimum": 1},
                        "schedule_type": {
                            "type": "string",
                            "enum": ["cron", "interval"],
                            "default": "cron",
                        },
                        "cron_expression": {"type": "string", "min_length": 1},
                        "interval_seconds": {"type": "integer", "minimum": 1},
                        "timezone": {"type": "string", "min_length": 1, "default": "UTC"},
                        "status": {
                            "type": "string",
                            "enum": ["active", "paused"],
                            "default": "active",
                        },
                        "input_payload": {"one_of": [{"type": "object"}, {"type": "null"}]},
                        "input_prompt": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "max_retries": {"type": "integer", "minimum": 0, "default": 0},
                        "retry_backoff_seconds": {"type": "integer", "minimum": 0, "default": 60},
                    },
                    "constraints": {"required": ["target_type", "target_id"]},
                },
                "update": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "target_type": {"type": "string", "enum": ["function", "agent"]},
                        "target_id": {"type": "integer", "minimum": 1},
                        "schedule_type": {"type": "string", "enum": ["cron", "interval"]},
                        "cron_expression": {"type": "string", "min_length": 1},
                        "interval_seconds": {"type": "integer", "minimum": 1},
                        "timezone": {"type": "string", "min_length": 1},
                        "status": {"type": "string", "enum": ["active", "paused"]},
                        "input_payload": {"one_of": [{"type": "object"}, {"type": "null"}]},
                        "input_prompt": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "max_retries": {"type": "integer", "minimum": 0},
                        "retry_backoff_seconds": {"type": "integer", "minimum": 0},
                    },
                },
            },
            "datasource": {
                "create": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "host": {"type": "string", "min_length": 1},
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "db_type": {"type": "string", "min_length": 1, "default": "mysql"},
                        "cluster_key": {"type": "string", "min_length": 1},
                        "tenant_role": {
                            "type": "string",
                            "enum": ["user", "sys"],
                            "default": "user",
                        },
                        "tenant_identifier": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "attributes": {"one_of": [{"type": "object"}, {"type": "null"}]},
                        "user": {"type": "string", "min_length": 1},
                        "password": {"type": "string", "min_length": 1},
                        "database": {"type": "string", "min_length": 1},
                        "status": {
                            "type": "string",
                            "enum": ["active", "inactive"],
                            "default": "active",
                        },
                    },
                    "constraints": {
                        "required": ["name", "host", "port", "user", "password", "database"]
                    },
                },
                "update": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "host": {"type": "string", "min_length": 1},
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "db_type": {"type": "string", "min_length": 1},
                        "cluster_key": {"type": "string", "min_length": 1},
                        "tenant_role": {"type": "string", "enum": ["user", "sys"]},
                        "tenant_identifier": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "attributes": {"one_of": [{"type": "object"}, {"type": "null"}]},
                        "user": {"type": "string", "min_length": 1},
                        "password": {"type": "string", "min_length": 1},
                        "database": {"type": "string", "min_length": 1},
                        "status": {"type": "string", "enum": ["active", "inactive"]},
                    },
                },
            },
            "channel": {
                "create": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "provider": {
                            "type": "string",
                            "enum": ["dingtalk", "feishu", "wechat"],
                            "default": "dingtalk",
                        },
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "status": {
                            "type": "string",
                            "enum": ["active", "inactive"],
                            "default": "active",
                        },
                        "config": {"type": "object"},
                        "webhook_url": {"type": "string", "min_length": 1},
                        "security": {"type": "object"},
                        "template": {"type": "object"},
                    },
                    "constraints": {"required": ["name"]},
                },
                "update": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "name": {"type": "string", "min_length": 1},
                        "provider": {
                            "type": "string",
                            "enum": ["dingtalk", "feishu", "wechat", "slack", "telegram"],
                        },
                        "description": {"one_of": [{"type": "string"}, {"type": "null"}]},
                        "status": {"type": "string", "enum": ["active", "inactive"]},
                        "config": {"type": "object"},
                        "webhook_url": {"type": "string", "min_length": 1},
                        "security": {"type": "object"},
                        "template": {"type": "object"},
                    },
                },
            },
            "scheduler_history": {
                "list": {"$ref": "scheduler_history_api.schemas.list"},
                "delete": {"$ref": "scheduler_history_api.schemas.delete"},
            },
        },
        "operate_payload_schemas": {
            "page": {
                "preview": {"type": "object", "additional_properties": False, "properties": {}},
                "publish": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "artifact_payload": {"type": "object"},
                        "artifact_uri": {"type": "string", "min_length": 1},
                        "release_notes": {"one_of": [{"type": "string"}, {"type": "null"}]},
                    },
                },
                "archive": {"type": "object", "additional_properties": False, "properties": {}},
                "rollback": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {"release_id": {"type": "integer", "minimum": 1}},
                    "constraints": {"required": ["release_id"]},
                },
            },
            "function": {
                "strategy": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "requirement": {"type": "string", "min_length": 1},
                        "contract": {"type": "object"},
                        "force_strategy": {"type": "string", "enum": ["reuse", "extend", "create"]},
                        "reuse_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                        "extend_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                "verify": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "code_snapshot": {"type": "string", "min_length": 1},
                        "dependency_manifest": {"type": "object"},
                    },
                },
                "release": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "code_snapshot": {"type": "string", "min_length": 1},
                        "requirement": {"type": "string", "min_length": 1},
                        "contract": {"type": "object"},
                        "force_strategy": {"type": "string", "enum": ["reuse", "extend", "create"]},
                        "reuse_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                        "extend_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                        "dependency_manifest": {"type": "object"},
                        "release_metadata": {"type": "object"},
                    },
                },
                "invoke": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "payload": {"type": "object"},
                        "datasource_id": {"type": "integer", "minimum": 1},
                        "scope_metadata": {"type": "object"},
                        "timeout_seconds": {"type": "number", "minimum": 0.001},
                        "trace_id": {"type": "string", "min_length": 1},
                    },
                },
            },
            "scheduler": {
                "pause": {"type": "object", "additional_properties": False, "properties": {}},
                "resume": {"type": "object", "additional_properties": False, "properties": {}},
                "run-now": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {"trace_id": {"type": "string", "min_length": 1}},
                },
                "list-runs": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20}
                    },
                },
            },
            "datasource": {},
            "channel": {
                "send": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "message": {"type": "object"},
                        "template": {"type": "object"},
                        "message_type": {
                            "type": "string",
                            "enum": ["text", "markdown", "actionCard", "feedCard"],
                        },
                        "title": {"type": "string", "min_length": 1},
                        "content": {"type": "string", "min_length": 1},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                }
            },
        },
    },
    "scheduler_history_api": {
        "main_helper": "scheduler_history",
        "class_helper": "self.scheduler_history",
        "methods": [
            "list(where=?, limit=?)",
            "delete(where=?, policy=?, dry_run=?)",
        ],
        "status_enum": ["queued", "running", "retrying", "success", "failed"],
        "schemas": {
            "where": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "schedule_id": {"type": "integer", "minimum": 1},
                    "statuses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["queued", "running", "retrying", "success", "failed"],
                        },
                        "min_items": 1,
                    },
                },
            },
            "policy": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "retention_seconds": {"type": "integer", "minimum": 1},
                    "keep_latest": {"type": "integer", "minimum": 0},
                },
                "constraints": {
                    "at_least_one_of": ["retention_seconds", "keep_latest"],
                },
            },
            "list": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "where": {"$ref": "scheduler_history_api.schemas.where"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
                },
            },
            "delete": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "where": {"$ref": "scheduler_history_api.schemas.where"},
                    "policy": {"$ref": "scheduler_history_api.schemas.policy"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "constraints": {
                    "required": ["policy"],
                    "delete_scope_requires_any_of": [
                        "where.schedule_id",
                        "where.statuses",
                        "policy.retention_seconds",
                        "policy.keep_latest",
                    ],
                },
            },
        },
        "plan_mode_policy": {
            "delete_requires_dry_run": True,
        },
    },
    "forbidden": {
        "imports": ["praxis"],
        "assistant_message_terms": [
            "get_session_by_id",
            "SQLAlchemy",
            "text()",
            "mappings()",
            "row index",
            "Database key",
        ],
        "placeholder_output": "no mock/fake/placeholder datasource data",
    },
}


def get_function_runtime_contract() -> dict[str, Any]:
    # Return a detached copy so callers cannot mutate the source constant.
    return json.loads(json.dumps(_FUNCTION_RUNTIME_CONTRACT, ensure_ascii=False))


def get_function_runtime_contract_json() -> str:
    return json.dumps(_FUNCTION_RUNTIME_CONTRACT, ensure_ascii=False, sort_keys=True)


def get_function_runtime_contract_block() -> str:
    return f"Fixed Runtime Contract (JSON):\n{get_function_runtime_contract_json()}"
