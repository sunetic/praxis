"""A diagnostic instruction comparison must not change history or tool schemas."""

from copy import deepcopy

import pytest

from app.services.agent.definitions import CHAT_INSTRUCTIONS
from tools.agent_runtime_instruction_probe import CANDIDATE, with_instructions


def test_only_current_system_prefix_changes_and_source_is_not_mutated():
    request = {
        "model": "configured-model",
        "messages": [
            {"role": "system", "content": CHAT_INSTRUCTIONS + "\nScene instructions"},
            {"role": "assistant", "content": "An old assertion"},
            {"role": "tool", "content": '{"database":"mysql"}'},
            {"role": "user", "content": "Explain without editing"},
        ],
        "tools": [{"type": "function", "function": {"name": "write"}}],
        "stream": True,
    }
    original = deepcopy(request)
    changed = with_instructions(request, CANDIDATE)
    assert request == original
    assert changed["messages"][0]["content"] == CANDIDATE + "\nScene instructions"
    changed["messages"][0] = original["messages"][0]
    assert changed == original
    assert with_instructions(request, CHAT_INSTRUCTIONS) == original


@pytest.mark.parametrize("role,content", [("user", CHAT_INSTRUCTIONS), ("system", "Unexpected")])
def test_different_source_instructions_are_rejected(role, content):
    with pytest.raises(ValueError):
        with_instructions({"messages": [{"role": role, "content": content}]}, CANDIDATE)
