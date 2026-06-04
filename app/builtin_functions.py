"""CE built-in functions — registered by tools/seed.py on first run."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import models

logger = logging.getLogger(__name__)

_SCHEDULER_HISTORY_CLEANUP_CODE = '''\
"""Built-in Function: Clean up stale Scheduler history records.

Payload:
    retention_hours: int   Keep records newer than this many hours (default 168 = 7 days).

Supports plan/apply execution modes via context['execution_mode']:
    plan  — dry run, reports how many records would be deleted without touching data.
    apply — deletes records that exceed the retention window.
"""

FUNCTION_META = {
    "name": "Scheduler History Cleanup",
    "kind": "builtin",
    "slug": "scheduler-history-cleanup",
    "dependencies": {},
}


def main(payload, context):
    retention_hours = payload.get("retention_hours", 168)
    retention_seconds = int(retention_hours * 3600)

    execution_mode = context.get("execution_mode", "plan")
    dry_run = execution_mode == "plan"

    schedules = platform.list("scheduler")

    results = []
    for sched in schedules:
        schedule_id = sched.get("id")
        if not schedule_id:
            continue

        deleted = scheduler_history.delete(
            where={"schedule_id": schedule_id},
            policy={"retention_seconds": retention_seconds},
            dry_run=dry_run,
        )
        results.append(
            {
                "schedule_id": schedule_id,
                "deleted_count": deleted.get("deleted_count", 0),
                "dry_run": dry_run,
            }
        )

    return {"results": results}
'''

_CE_BUILTINS: list[dict[str, Any]] = [
    {
        "name": "Scheduler History Cleanup",
        "description": "Automatically clean up scheduler history records older than 7 days. Supports dry-run and actual execution modes.",
        "slug": "scheduler-history-cleanup",
        "code": _SCHEDULER_HISTORY_CLEANUP_CODE,
        "schedule": {
            "name": "Scheduled History Cleanup",
            "cron_expression": "0 2 * * *",
        },
    },
]

_LEGACY_NAMES = {
    "清理调度器历史记录",
    "定时计划历史清理",
}


def register_builtin_functions(db: Session) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    created = 0
    updated = 0

    for spec in _CE_BUILTINS:
        fn = (
            db.query(models.Function)
            .filter(models.Function.slug == spec["slug"])
            .first()
        )
        if fn is None:
            fn = (
                db.query(models.Function)
                .filter(
                    models.Function.kind.in_(["built_in", "builtin"]),
                    models.Function.name.in_(_LEGACY_NAMES | {spec["name"]}),
                )
                .first()
            )

        if fn is None:
            fn = models.Function(
                name=spec["name"],
                description=spec["description"],
                slug=spec["slug"],
                kind="built_in",
                status="released",
                draft_code=spec["code"],
                created_at=now,
                updated_at=now,
            )
            db.add(fn)
            db.flush()

            release = models.FunctionRelease(
                function_id=fn.id,
                version=1,
                code_snapshot=spec["code"],
                created_at=now,
            )
            db.add(release)
            db.flush()
            fn.current_release_id = release.id
            created += 1
        else:
            changed = False
            if fn.name != spec["name"]:
                fn.name = spec["name"]
                changed = True
            if fn.description != spec["description"]:
                fn.description = spec["description"]
                changed = True
            if fn.slug != spec["slug"]:
                fn.slug = spec["slug"]
                changed = True
            if fn.kind not in ("built_in", "builtin"):
                fn.kind = "built_in"
                changed = True
            if changed:
                fn.updated_at = now
                updated += 1

        if "schedule" in spec:
            sched_spec = spec["schedule"]
            sched = (
                db.query(models.Schedule)
                .filter(models.Schedule.function_id == fn.id)
                .first()
            )
            if sched is None:
                sched = (
                    db.query(models.Schedule)
                    .filter(
                        models.Schedule.kind.in_(["built_in", "builtin"]),
                        models.Schedule.name.in_(_LEGACY_NAMES | {sched_spec["name"]}),
                    )
                    .first()
                )

            if sched is None:
                sched = models.Schedule(
                    name=sched_spec["name"],
                    kind="built_in",
                    status="active",
                    target_type="function",
                    function_id=fn.id,
                    schedule_type="cron",
                    cron_expression=sched_spec.get("cron_expression", "0 2 * * *"),
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                )
                db.add(sched)
            else:
                if sched.name != sched_spec["name"]:
                    sched.name = sched_spec["name"]
                    sched.updated_at = now
                if sched.function_id != fn.id:
                    sched.function_id = fn.id
                    sched.updated_at = now

    db.commit()
    return {"created": created, "updated": updated}
