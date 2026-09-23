"""One-shot schedule configuration extraction, not an Agent execution loop."""

import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, with_config
from typing_extensions import TypedDict

from app.services.agent.models import ModelFactory


@with_config(ConfigDict(extra="forbid", strict=True))
class SchedulePatch(TypedDict, total=False):
    # An omitted field is unchanged. Null only clears an inactive timing field.
    # The same typed definition drives both provider schema and server validation.
    schedule_type: Literal["cron", "interval"]
    cron_expression: (
        Annotated[
            str,
            Field(
                min_length=1,
                description="Five fields: minute (0-59), hour (0-23), day-of-month, month, weekday. The time is local to timezone; 0/7=Sunday, 1=Monday through 6=Saturday.",
            ),
        ]
        | None
    )
    interval_seconds: Annotated[int, Field(ge=1)] | None
    max_retries: Annotated[int, Field(ge=0)]
    status: Literal["active", "paused"]
    timezone: Annotated[str, Field(min_length=1)]


class ScheduleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    patch: SchedulePatch
    summary: str = Field(min_length=1)


@dataclass(frozen=True)
class SchedulerBuildResult:
    patch: dict
    summary: str


class SchedulerBuilderService:
    def __init__(self, models: ModelFactory):
        self.models = models

    async def apply_prompt(self, prompt: str, current: dict) -> SchedulerBuildResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        # Do not send unrelated business payloads or credentials to an extractor.
        projected = {
            key: current.get(key)
            for key in (
                "target_type",
                "schedule_type",
                "cron_expression",
                "interval_seconds",
                "max_retries",
                "status",
                "timezone",
            )
        }
        proposal = await self.models.structured(
            purpose="schedule_configuration",
            result_type=ScheduleProposal,
            instructions=(
                "Convert the user's schedule configuration request into a minimal patch. "
                "Return the proposed fields with a concise summary in the user's language. "
                "Preserve fields the user did not ask to change. Never infer permission to change the target or business input. "
                "For interval schedules include a positive interval_seconds and null cron_expression; "
                "for cron schedules include a cron_expression and null interval_seconds. "
                "Use an IANA timezone. Agent schedules must have max_retries=0; whole-Agent retries are not supported. "
                "Cron has five fields; weekdays use names or 0/7=Sunday, 1=Monday through 6=Saturday. "
                "Day-of-month and weekday restrictions both apply. "
                "Do not claim this proposal has already been saved or executed."
            ),
            prompt=json.dumps(
                {
                    "request": prompt,
                    "current": projected,
                },
                ensure_ascii=False,
            ),
        )
        return SchedulerBuildResult(patch=proposal.patch, summary=proposal.summary)
