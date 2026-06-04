from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduleRuntimeResult:
    run_id: str
    status: str
    output: Any | None
    output_summary: str | None
    error_class: str | None
    error_message: str | None
    duration_ms: int
    conversation_id: int | None = None
