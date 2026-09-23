"""Small guards for provider responses that violate the native tool protocol."""

import re
from collections.abc import Collection

_TEXTUAL_INVOKE = re.compile(
    r"<invoke\s+name\s*=\s*(['\"])(?P<name>[^'\"]+)\1",
    re.IGNORECASE,
)


def textual_tool_call(text: str, tool_names: Collection[str]) -> str | None:
    """Return a known tool serialized as XML instead of a native tool call.

    This is detection only. Text is never parsed into arguments or dispatched.
    """
    known = set(tool_names)
    for match in _TEXTUAL_INVOKE.finditer(text):
        name = match.group("name")
        if name in known:
            return name
    return None
