"""Boot-time wiring for built-ins, scene agents, and schedule targets."""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger("app.bootstrap")


def bootstrap() -> None:
    _register_builtins()
    _register_scene_agents()
    _register_schedule_targets()
    _ensure_stats_analysis_schedule()
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
    from app.services.chat.scene_agents.session_transaction import SessionTransactionAgent
    from app.services.chat.scene_agents.stats_analysis import StatsAnalysisAgent
    from app.services.page.chat_agent import PageChatAgent

    register_scene_agent(PageChatAgent())
    register_scene_agent(SessionTransactionAgent())
    register_scene_agent(StatsAnalysisAgent())
    logger.info("scene_agents_registered count=3")


def _register_schedule_targets() -> None:
    try:
        from app.api.schedules import register_schedule_target
        from app.services.scheduler.runtime import register_schedule_type_handler

        register_schedule_target("stats_analysis", internal=True)
        register_schedule_target("collector", internal=True)
        register_schedule_type_handler("stats_analysis", _handle_stats_analysis_schedule)
        register_schedule_type_handler("collector", _handle_collector_schedule)
        logger.info("schedule_targets_registered targets=stats_analysis,collector")
    except Exception as exc:
        logger.warning("schedule_targets_bootstrap_failed error=%s", exc)


def _ensure_stats_analysis_schedule() -> None:
    try:
        from app.api import stats_analysis

        outcome = stats_analysis.ensure_stats_analysis_schedule_singleton()
        logger.info(
            "stats_analysis_schedule_bootstrap_done created=%s removed_legacy=%s",
            outcome.get("created", 0),
            outcome.get("removed_legacy", 0),
        )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("stats_analysis_schedule_bootstrap_failed error=%s", exc)


async def _handle_stats_analysis_schedule(
    runtime_service, schedule, *, payload, trigger_type, trace_id
):
    from datetime import datetime

    from app.api.stats_analysis import (
        _list_schedulable_datasource_ids,
        run_stats_risk_collect_for_datasource,
    )
    from app.services.scheduler.result import ScheduleRuntimeResult

    mode = str(payload.get("mode") or "").strip().lower()
    datasource_id = payload.get("datasource_id")
    if not isinstance(datasource_id, int):
        datasource_id = (
            None
            if mode == "batch"
            else (schedule.target_id if isinstance(schedule.target_id, int) else None)
        )
    lookback_days = int(payload.get("lookback_days") or 7)
    stale_days = int(payload.get("stale_days") or 7)
    trigger_type_str = str(payload.get("trigger_type") or "auto")
    started_at = datetime.utcnow()
    try:
        if isinstance(datasource_id, int):
            result = await run_stats_risk_collect_for_datasource(
                datasource_id,
                lookback_days=lookback_days,
                stale_days=stale_days,
                trigger_type=trigger_type_str,
            )
            return ScheduleRuntimeResult(
                run_id=trace_id or "",
                status="success",
                output=result.model_dump(),
                output_summary=None,
                error_class=None,
                error_message=None,
                duration_ms=max(int((datetime.utcnow() - started_at).total_seconds() * 1000), 0),
            )

        db = runtime_service._session_factory()
        try:
            datasource_ids = _list_schedulable_datasource_ids(db)
        finally:
            db.close()
        max_datasources = int(payload.get("max_datasources") or 20)
        if max_datasources > 0:
            datasource_ids = datasource_ids[:max_datasources]
        items, failures = [], 0
        total_collected = total_active = total_expired = 0
        for ds_id in datasource_ids:
            try:
                item_result = await run_stats_risk_collect_for_datasource(
                    ds_id,
                    lookback_days=lookback_days,
                    stale_days=stale_days,
                    trigger_type=trigger_type_str,
                )
                item = item_result.model_dump()
                item["status"] = "success"
                total_collected += int(item_result.collected_tables)
                total_active += int(item_result.active_candidates)
                total_expired += int(item_result.expired_candidates)
            except Exception as exc:
                failures += 1
                item = {
                    "datasource_id": ds_id,
                    "status": "failed",
                    "error_summary": str(exc),
                    "collected_tables": 0,
                    "active_candidates": 0,
                    "expired_candidates": 0,
                }
            items.append(item)

        status = (
            "success"
            if failures == 0
            else ("success" if failures < len(datasource_ids) else "failed")
        )
        return ScheduleRuntimeResult(
            run_id=trace_id or "",
            status=status,
            output={
                "mode": "batch",
                "datasource_count": len(datasource_ids),
                "succeeded": len(datasource_ids) - failures,
                "failed": failures,
                "collected_tables": total_collected,
                "active_candidates": total_active,
                "expired_candidates": total_expired,
                "items": items,
            },
            output_summary=None,
            error_class=None if status == "success" else "runtime",
            error_message=None
            if status == "success"
            else "stats_analysis batch collect failed for all datasources",
            duration_ms=max(int((datetime.utcnow() - started_at).total_seconds() * 1000), 0),
        )
    except Exception as exc:
        return ScheduleRuntimeResult(
            run_id=trace_id or "",
            status="failed",
            output=None,
            output_summary=None,
            error_class="runtime",
            error_message=str(exc),
            duration_ms=max(int((datetime.utcnow() - started_at).total_seconds() * 1000), 0),
        )


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
