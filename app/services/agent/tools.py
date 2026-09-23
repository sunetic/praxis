"""Explicit capability selection and validation for dynamic tool schemas."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from pydantic_ai import ModelRetry, RunContext, Tool
from referencing import Registry

from app.services.agent.definitions import RunDependencies


def select_tools(
    catalog: Mapping[str, Tool[RunDependencies]],
    configured: frozenset[str],
    authorized: frozenset[str],
) -> list[Tool[RunDependencies]]:
    """Select the only tools the framework may execute; empty never means all."""
    unknown = configured.difference(catalog)
    if unknown:
        raise ValueError(f"Unknown configured tools: {', '.join(sorted(unknown))}")
    selected = []
    for name in sorted(configured & authorized):
        tool = catalog[name]
        if tool.name != name:
            raise ValueError(f"Tool catalog key does not match tool name: {name}")
        selected.append(tool)
    return selected


def dynamic_tool(
    function: Callable[..., Any],
    *,
    name: str,
    description: str,
    schema: dict[str, Any],
    sequential: bool = False,
) -> Tool[RunDependencies]:
    """Validate service JSON arguments explicitly: Tool.from_schema skips validation.

    Functions receive trusted RunContext first. Resource authorization and approval
    remain the responsibility of that function, and are rechecked on execution.
    """
    schema = deepcopy(schema)
    Draft202012Validator.check_schema(schema)
    # Never fetch schema references from the network while validating model input.
    # Local $defs references are supported; unresolved external references fail closed.
    validator = Draft202012Validator(schema, registry=Registry())

    def validate_args(_ctx: RunContext[RunDependencies], **arguments: Any) -> None:
        try:
            validator.validate(arguments)
        except ValidationError as exc:
            # Do not echo potentially sensitive argument values in protocol errors.
            path = ".".join(str(part) for part in exc.absolute_path) or "arguments"
            raise ModelRetry(f"Invalid {path}: JSON schema {exc.validator} constraint") from exc

    return Tool.from_schema(
        function,
        name=name,
        description=description,
        json_schema=schema,
        takes_ctx=True,
        sequential=sequential,
        args_validator=validate_args,
    )
