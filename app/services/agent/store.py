"""Stable facade for agent run persistence."""

from app.services.agent.store_base import (
    BUDGET_ADAPTER,
    DEFERRED_ADAPTER,
    TERMINAL_STATUSES,
    CallChangedError,
    LeaseLostError,
    OutcomeUnknownError,
    ResourceBusyError,
    RunConflictError,
    RunNotFoundError,
    definition_json,
    fingerprint,
)
from app.services.agent.store_calls import ToolCallStoreMixin
from app.services.agent.store_conversations import ConversationStoreMixin
from app.services.agent.store_execution import ExecutionStoreMixin


class RunStore(ConversationStoreMixin, ToolCallStoreMixin, ExecutionStoreMixin):
    """Facade preserving the persistence API while separating its responsibilities."""


__all__ = [
    "BUDGET_ADAPTER",
    "DEFERRED_ADAPTER",
    "TERMINAL_STATUSES",
    "CallChangedError",
    "LeaseLostError",
    "OutcomeUnknownError",
    "ResourceBusyError",
    "RunConflictError",
    "RunNotFoundError",
    "RunStore",
    "definition_json",
    "fingerprint",
]
