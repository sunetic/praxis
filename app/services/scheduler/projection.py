"""Read native execution facts without a second persisted Agent state machine."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import agent_runs as native
from app.models.models import ScheduleRun
from app.services.agent.store import TERMINAL_STATUSES


def project_schedule_run(db: Session, record: ScheduleRun) -> dict:
    result = json.loads(
        json.dumps(
            {column.name: getattr(record, column.name) for column in record.__table__.columns},
            default=str,
            ensure_ascii=False,
        )
    )
    if record.target_type != "agent" or not record.conversation_id:
        return result
    run = (
        db.execute(
            select(native.runs).where(
                native.runs.c.conversation_id == record.conversation_id,
                native.runs.c.client_request_id == record.run_id,
            )
        )
        .mappings()
        .first()
    )
    if run is None:
        return result
    result.update(
        runtime_run_id=run["id"],
        status=run["status"],
        runtime_status=run["status"],
        output_summary=run["output"],
        output_payload={
            "assistant_message": run["output"],
            "conversation_id": record.conversation_id,
        },
        error_summary=run["error_code"],
        finished_at=None,
    )
    if run["status"] in TERMINAL_STATUSES:
        # The terminal native event, not scheduler submission time, is the end.
        ended = db.execute(
            select(native.events.c.created_at).where(
                native.events.c.run_id == run["id"],
                native.events.c.seq == run["event_seq"],
            )
        ).scalar_one_or_none()
        if ended is not None:
            result["finished_at"] = datetime.fromtimestamp(ended, UTC).isoformat()
    return result
