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
