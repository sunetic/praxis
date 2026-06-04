from __future__ import annotations

from typing import Any


class JsonSchemaValidationError(ValueError):
    pass


def validate_json_object(*, schema: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise JsonSchemaValidationError("schema must be an object")
    if str(schema.get("type") or "").strip() != "object":
        raise JsonSchemaValidationError("only object schema is supported")
    if not isinstance(payload, dict):
        raise JsonSchemaValidationError("payload must be an object")

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise JsonSchemaValidationError("schema.properties must be an object")
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    additional_properties = bool(schema.get("additionalProperties", True))

    normalized: dict[str, Any] = {}
    for name in required:
        key = str(name or "").strip()
        if key and key not in payload:
            raise JsonSchemaValidationError(f"{key} is missing")

    for key, value in payload.items():
        if key not in properties:
            if additional_properties:
                normalized[key] = value
                continue
            raise JsonSchemaValidationError(f"{key} is not an allowed field")
        normalized[key] = _validate_node(
            field=key,
            schema=properties[key] if isinstance(properties.get(key), dict) else {},
            value=value,
        )
    return normalized


def _validate_node(*, field: str, schema: dict[str, Any], value: Any) -> Any:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        normalized_types = [str(item).strip() for item in expected_type if str(item).strip()]
    else:
        normalized_types = [str(expected_type).strip()] if str(expected_type or "").strip() else []

    if normalized_types:
        converted, matched = _coerce_value_by_types(value=value, types=normalized_types)
        if not matched:
            readable = "|".join(normalized_types)
            raise JsonSchemaValidationError(f"{field} has invalid type, expected {readable}")
        value = converted

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        raise JsonSchemaValidationError(f"{field} must be one of {enum_values}")

    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and isinstance(value, (int, float)) and value < minimum:
        raise JsonSchemaValidationError(f"{field} must not be less than {minimum}")

    min_length = schema.get("minLength")
    if isinstance(min_length, int) and isinstance(value, str) and len(value) < min_length:
        raise JsonSchemaValidationError(f"{field} length must not be less than {min_length}")

    return value


def _coerce_value_by_types(*, value: Any, types: list[str]) -> tuple[Any, bool]:
    for node_type in types:
        if node_type == "null":
            if value is None:
                return None, True
            continue
        if node_type == "string":
            if isinstance(value, str):
                return value, True
            continue
        if node_type == "integer":
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value, True
            if isinstance(value, str):
                text = value.strip()
                if text and (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
                    return int(text), True
            continue
        if node_type == "number":
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return value, True
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    continue
                try:
                    return float(text), True
                except ValueError:
                    continue
            continue
        if node_type == "boolean":
            if isinstance(value, bool):
                return value, True
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes"}:
                    return True, True
                if lowered in {"false", "0", "no"}:
                    return False, True
            continue
        if node_type == "object":
            if isinstance(value, dict):
                return value, True
            continue
        if node_type == "array":
            if isinstance(value, list):
                return value, True
            continue
    return value, False
