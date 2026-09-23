"""Submit a scheduled occurrence to the application-owned native runtime."""

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import insert, select, update

from app.models import agent_runs as native
from app.models.models import ScheduleRun
from app.services.agent.persistence import run_db
from app.services.agent.store import RunConflictError
from app.services.scheduler.result import ScheduleRuntimeResult

if TYPE_CHECKING:
    from app.services.agent.application import RuntimeApplication


class ScheduledAgentRunner:
    def __init__(self, application: "RuntimeApplication"):
        self.application = application

    async def invoke(
        self,
        *,
        agent_id: int,
        prompt: str,
        schedule_run_id: str,
        datasource_id: int | None = None,
    ) -> ScheduleRuntimeResult:
        return await run_db(
            self._submit,
            agent_id=agent_id,
            prompt=prompt,
            schedule_run_id=schedule_run_id,
            datasource_id=datasource_id,
        )

    def _submit(
        self,
        *,
        agent_id: int,
        prompt: str,
        schedule_run_id: str,
        datasource_id: int | None = None,
    ) -> ScheduleRuntimeResult:
        """Return acceptance, not task success. The native run owns execution.

        Persist the occurrence's conversation before submission. If the process
        stops between submission and the caller saving the returned run ID, the
        native run is still recoverable by conversation + occurrence request ID.
        There is no second execution owner, timeout loop, or HTTP/SSE adapter.
        """
        app = self.application
        scene = {
            "agent_id": agent_id,
            "datasource_ids": ([datasource_id] if datasource_id is not None else None),
        }
        conversation_id = uuid5(NAMESPACE_URL, f"praxis:schedule-run:{schedule_run_id}").hex
        with app.sessions.begin() as db:
            occurrence = db.execute(
                update(ScheduleRun)
                .where(ScheduleRun.run_id == schedule_run_id, ScheduleRun.target_type == "agent")
                .values(status=ScheduleRun.status)
                .returning(ScheduleRun.id, ScheduleRun.schedule_id, ScheduleRun.conversation_id)
            ).first()
            if occurrence is None:
                raise ValueError("Scheduled Agent requires a persisted occurrence")
            if occurrence.conversation_id and occurrence.conversation_id != conversation_id:
                raise RunConflictError("Scheduled occurrence has another conversation")
            if not occurrence.conversation_id:
                db.execute(
                    insert(native.conversations).values(
                        id=conversation_id,
                        actor_id="local",
                        title=f"Schedule {occurrence.schedule_id}",
                        scene=scene,
                        created_at=app.store.clock(),
                    )
                )
                db.execute(
                    update(ScheduleRun)
                    .where(ScheduleRun.id == occurrence.id)
                    .values(
                        conversation_id=conversation_id,
                    )
                )
            existing = (
                db.execute(
                    select(native.runs).where(
                        native.runs.c.conversation_id == conversation_id,
                        native.runs.c.client_request_id == schedule_run_id,
                    )
                )
                .mappings()
                .first()
            )
            saved_scene = db.execute(
                select(native.conversations.c.scene).where(
                    native.conversations.c.id == conversation_id
                )
            ).scalar_one()
            if saved_scene != scene or (existing and existing["prompt"] != prompt):
                raise RunConflictError(
                    "Scheduled occurrence was already submitted with other input"
                )
        if existing:
            return scheduled_result(dict(existing))
        definition = app.resolve(conversation_id, "local", {})
        definition = replace(
            definition,
            scope={
                **definition.scope,
                "entrypoint": "scheduler",
                "schedule_id": occurrence.schedule_id,
                "schedule_run_id": schedule_run_id,
            },
        )
        run = app.service.submit(conversation_id, "local", schedule_run_id, prompt, definition)
        return scheduled_result(run)


def scheduled_result(run: dict) -> ScheduleRuntimeResult:
    return ScheduleRuntimeResult(
        run_id=run["id"],
        status=run["status"],
        output={"assistant_message": run["output"], "conversation_id": run["conversation_id"]},
        output_summary=run["output"],
        error_class=run["error_code"],
        error_message=run["error_code"],
        duration_ms=int(run["budget"].get("active_seconds", 0) * 1000),
        conversation_id=run["conversation_id"],
    )
