"""Boot-time wiring for built-ins, scene agents, and schedule targets."""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger("app.bootstrap")


def bootstrap() -> None:
    _register_builtins()
    _register_scene_agents()
    _register_schedule_targets()
    logger.info("bootstrap_done")


def _register_builtins() -> None:
    try:
        from app.builtin_agents import register_builtin_agents
        from app.builtin_functions import register_builtin_functions
        from app.db.database import SessionLocal

        with SessionLocal() as db:
            registered_fns = register_builtin_functions(db)
            registered_agents = register_builtin_agents(db)
            logger.info(
                "builtins_bootstrap_done functions=%s agents=%s",
                len(registered_fns),
                len(registered_agents),
            )
    except Exception as exc:
        logger.warning("builtins_bootstrap_failed error=%s", exc)
    try:
        from app.builtin_knowledge import register_builtin_knowledge_packs

        register_builtin_knowledge_packs()
    except Exception as exc:
        logger.warning("builtin_knowledge_packs_bootstrap_failed error=%s", exc)


def _register_scene_agents() -> None:
    from app.services.chat.scene_agents.registry import register_scene_agent
    from app.services.page.chat_agent import PageChatAgent

    register_scene_agent(PageChatAgent())
    logger.info("scene_agents_registered count=1")


def _register_schedule_targets() -> None:
    try:
        from app.api.schedules import register_schedule_target
        from app.services.scheduler.runtime import register_schedule_type_handler

        register_schedule_target("collector", internal=True)
        register_schedule_type_handler("collector", _handle_collector_schedule)
        logger.info("schedule_targets_registered targets=collector")
    except Exception as exc:
        logger.warning("schedule_targets_bootstrap_failed error=%s", exc)


async def _handle_collector_schedule(runtime_service, schedule, *, payload, trigger_type, trace_id):
    from app.services.collector.runtime import CollectorRuntimeService
    from app.services.scheduler.result import ScheduleRuntimeResult

    try:
        collector_runtime = CollectorRuntimeService(
            session_factory=runtime_service._session_factory
        )
        payload.update(
            {"schedule_id": schedule.id, "trigger_type": trigger_type, "trace_id": trace_id}
        )
        return await collector_runtime.invoke(schedule, payload=payload, trace_id=trace_id)
    except Exception as exc:
        return ScheduleRuntimeResult(
            run_id=trace_id or "",
            status="failed",
            output=None,
            output_summary=None,
            error_class="runtime",
            error_message=str(exc),
            duration_ms=0,
        )
