from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import SessionLocal
from app.models import models
from app.services.channel_delivery import (
    ChannelDeliveryError,
    ChannelDeliveryService,
    normalize_channel_config,
    normalize_channel_provider,
    normalize_channel_status,
)
from app.services.function.identity import (
    generate_unique_function_slug,
    normalize_function_display_name,
    normalize_function_slug,
    validate_function_display_name,
)
from app.services.function.runtime import FunctionRuntimeService
from app.services.function.strategy import (
    FunctionStrategyDecider,
    FunctionVerificationHarness,
    StrategyThresholds,
)
from app.services.lifecycle import (
    FunctionLifecycleService,
    LifecycleValidationError,
    PageLifecycleService,
    PageState,
    ScheduleLifecycleService,
)
from app.services.scheduler.worker import SchedulerWorker

logger = get_logger("object.tools")

SCHEDULER_HISTORY_STATUS_ENUM = {"queued", "running", "retrying", "success", "failed"}


def _knowledge_doc_root(kb_id: int) -> str:
    return f"{Path(get_settings().data_dir).resolve() / 'knowledge' / str(kb_id)}/"


@dataclass
class ObjectToolError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class ObjectToolService:
    allowed_object_types = {
        "page",
        "function",
        "scheduler",
        "datasource",
        "scheduler_history",
        "channel",
        "knowledge_base",
        "knowledge_document",
        "service",
    }
    crud_actions = {"create", "read", "update", "delete", "list"}
    sensitive_actions = {
        "operate:publish",
        "operate:archive",
        "operate:rollback",
        "operate:release",
        "operate:pause",
        "operate:resume",
        "operate:run-now",
        "operate:send",
        "crud:delete",
    }

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Any = SessionLocal,
        page_lifecycle: PageLifecycleService | None = None,
        function_lifecycle: FunctionLifecycleService | None = None,
        schedule_lifecycle: ScheduleLifecycleService | None = None,
        function_strategy: FunctionStrategyDecider | None = None,
        function_verifier: FunctionVerificationHarness | None = None,
        channel_delivery: ChannelDeliveryService | None = None,
    ):
        self._session_factory = session_factory
        self._page_lifecycle = page_lifecycle or PageLifecycleService()
        self._function_lifecycle = function_lifecycle or FunctionLifecycleService()
        self._schedule_lifecycle = schedule_lifecycle or ScheduleLifecycleService()
        self._function_strategy = function_strategy or FunctionStrategyDecider()
        self._function_verifier = function_verifier or FunctionVerificationHarness()
        self._channel_delivery = channel_delivery or ChannelDeliveryService()

    async def crud(
        self,
        *,
        object_type: str,
        action: str,
        object_id: int | None = None,
        payload: dict[str, Any] | None = None,
        actor: str = "llm",
    ) -> dict[str, Any]:
        normalized_type = self._normalize_object_type(object_type)
        if action not in self.crud_actions:
            raise ObjectToolError(
                code="invalid_action",
                message=f"Unsupported CRUD action: {action}",
                details={"action": action, "allowed": sorted(self.crud_actions)},
            )
        payload = payload or {}
        return await self._execute_with_audit(
            object_type=normalized_type,
            action=f"crud:{action}",
            actor=actor,
            object_id=object_id,
            payload=payload,
            runner=lambda db: self._crud_impl(
                db,
                object_type=normalized_type,
                action=action,
                object_id=object_id,
                payload=payload,
            ),
        )

    async def operate(
        self,
        *,
        object_type: str,
        action: str,
        object_id: int,
        payload: dict[str, Any] | None = None,
        actor: str = "llm",
    ) -> dict[str, Any]:
        normalized_type = self._normalize_object_type(object_type)
        payload = payload or {}
        return await self._execute_with_audit(
            object_type=normalized_type,
            action=f"operate:{action}",
            actor=actor,
            object_id=object_id,
            payload=payload,
            runner=lambda db: self._operate_impl(
                db,
                object_type=normalized_type,
                action=action,
                object_id=object_id,
                payload=payload,
            ),
        )

    async def _execute_with_audit(
        self,
        *,
        object_type: str,
        action: str,
        actor: str,
        object_id: int | None,
        payload: dict[str, Any],
        runner: Any,
    ) -> dict[str, Any]:
        db = self._session_factory()
        object_id_text = str(object_id) if object_id is not None else "unknown"
        trace_id = str(payload.get("trace_id") or uuid.uuid4())
        logger.info(
            "object_tool_start %s",
            fmt_kv(
                trace_id=trace_id,
                object_type=object_type,
                action=action,
                object_id=object_id_text,
                actor=actor,
            ),
        )
        try:
            self._enforce_policy(action=action, actor=actor)
            result = await runner(db)
            object_id_text = str(result.get("id") or result.get("object_id") or object_id_text)
            self._write_audit(
                db,
                object_type=object_type,
                object_id=object_id_text,
                action=action,
                actor=actor,
                result="success",
                detail={
                    "request_payload": payload,
                    "response_summary": self._summarize(result),
                    "trace_id": trace_id,
                    "policy": {"sensitive": action in self.sensitive_actions, "actor": actor},
                },
            )
            db.commit()
            logger.info(
                "object_tool_success %s",
                fmt_kv(
                    trace_id=trace_id,
                    object_type=object_type,
                    action=action,
                    object_id=object_id_text,
                    actor=actor,
                ),
            )
            return result
        except ObjectToolError as err:
            db.rollback()
            self._write_audit(
                db,
                object_type=object_type,
                object_id=object_id_text,
                action=action,
                actor=actor,
                result="failure",
                detail={
                    "request_payload": payload,
                    "trace_id": trace_id,
                    "policy": {"sensitive": action in self.sensitive_actions, "actor": actor},
                    "error": {
                        "code": err.code,
                        "message": err.message,
                        "details": err.details or {},
                    },
                },
            )
            db.commit()
            logger.warning(
                "object_tool_failure %s",
                fmt_kv(
                    trace_id=trace_id,
                    object_type=object_type,
                    action=action,
                    object_id=object_id_text,
                    actor=actor,
                    error=err.code,
                ),
            )
            raise
        except LifecycleValidationError as err:
            db.rollback()
            normalized = ObjectToolError(code="lifecycle_constraint", message=str(err))
            self._write_audit(
                db,
                object_type=object_type,
                object_id=object_id_text,
                action=action,
                actor=actor,
                result="failure",
                detail={
                    "request_payload": payload,
                    "trace_id": trace_id,
                    "policy": {"sensitive": action in self.sensitive_actions, "actor": actor},
                    "error": {
                        "code": normalized.code,
                        "message": normalized.message,
                        "details": normalized.details or {},
                    },
                },
            )
            db.commit()
            logger.warning(
                "object_tool_lifecycle_failure %s",
                fmt_kv(
                    trace_id=trace_id,
                    object_type=object_type,
                    action=action,
                    object_id=object_id_text,
                    actor=actor,
                ),
            )
            raise normalized
        except Exception as err:  # pragma: no cover - defensive
            db.rollback()
            self._write_audit(
                db,
                object_type=object_type,
                object_id=object_id_text,
                action=action,
                actor=actor,
                result="failure",
                detail={
                    "request_payload": payload,
                    "trace_id": trace_id,
                    "policy": {"sensitive": action in self.sensitive_actions, "actor": actor},
                    "error": {"code": "internal_error", "message": str(err)},
                },
            )
            db.commit()
            logger.exception(
                "object_tool_internal_error %s",
                fmt_kv(
                    trace_id=trace_id,
                    object_type=object_type,
                    action=action,
                    object_id=object_id_text,
                    actor=actor,
                ),
            )
            raise ObjectToolError(code="internal_error", message=str(err))
        finally:
            db.close()

    async def _crud_impl(
        self,
        db: Session,
        *,
        object_type: str,
        action: str,
        object_id: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if object_type == "scheduler_history":
            return self._crud_scheduler_history(
                db,
                action=action,
                object_id=object_id,
                payload=payload,
            )

        if object_type in ("knowledge_base", "knowledge_document"):
            return self._crud_knowledge(
                db,
                object_type=object_type,
                action=action,
                object_id=object_id,
                payload=payload,
            )

        if object_type == "service":
            return self._crud_service(
                db,
                action=action,
                object_id=object_id,
            )

        model_cls = self._resolve_model(object_type)
        if action == "list":
            records = db.query(model_cls).limit(200).all()
            return {
                "object_type": object_type,
                "action": "list",
                "count": len(records),
                "items": [self._serialize_model(item) for item in records],
            }

        if action == "create":
            instance = self._create_object(db, object_type, payload)
            db.add(instance)
            db.commit()
            db.refresh(instance)
            return self._serialize_model(instance)

        if action == "read":
            if object_id is None:
                raise ObjectToolError(
                    code="missing_object_id", message="object_id is required for read"
                )
            item = db.query(model_cls).filter(model_cls.id == object_id).first()
            if item is None:
                raise ObjectToolError(
                    code="not_found",
                    message=f"{object_type} {object_id} not found",
                    details={"object_type": object_type, "object_id": object_id},
                )
            return self._serialize_model(item)

        if action == "update":
            if object_id is None:
                raise ObjectToolError(
                    code="missing_object_id", message="object_id is required for update"
                )
            item = db.query(model_cls).filter(model_cls.id == object_id).first()
            if item is None:
                raise ObjectToolError(
                    code="not_found",
                    message=f"{object_type} {object_id} not found",
                    details={"object_type": object_type, "object_id": object_id},
                )
            self._update_object(db, object_type, item, payload)
            db.commit()
            db.refresh(item)
            return self._serialize_model(item)

        if action == "delete":
            if object_id is None:
                raise ObjectToolError(
                    code="missing_object_id", message="object_id is required for delete"
                )
            item = db.query(model_cls).filter(model_cls.id == object_id).first()
            if item is None:
                raise ObjectToolError(
                    code="not_found",
                    message=f"{object_type} {object_id} not found",
                    details={"object_type": object_type, "object_id": object_id},
                )
            db.delete(item)
            db.commit()
            return {"object_type": object_type, "action": "delete", "object_id": object_id}

        raise ObjectToolError(code="invalid_action", message=f"Unsupported CRUD action: {action}")

    def _crud_scheduler_history(
        self,
        db: Session,
        *,
        action: str,
        object_id: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action == "create" or action == "update":
            raise ObjectToolError(
                code="invalid_action",
                message=f"scheduler_history does not support CRUD action '{action}'",
            )

        if action == "read":
            if object_id is None:
                raise ObjectToolError(
                    code="missing_object_id", message="object_id is required for read"
                )
            item = (
                db.query(models.ScheduleRun).filter(models.ScheduleRun.id == int(object_id)).first()
            )
            if item is None:
                raise ObjectToolError(
                    code="not_found",
                    message=f"scheduler_history {object_id} not found",
                    details={"object_type": "scheduler_history", "object_id": object_id},
                )
            return self._serialize_model(item)

        filters = self._normalize_scheduler_history_filters(payload, object_id=object_id)

        if action == "list":
            limit = filters["limit"]
            query = self._build_scheduler_history_query(db, filters=filters)
            records = (
                query.order_by(models.ScheduleRun.created_at.desc(), models.ScheduleRun.id.desc())
                .limit(limit)
                .all()
            )
            return {
                "object_type": "scheduler_history",
                "action": "list",
                "count": len(records),
                "filters": self._scheduler_history_filter_summary(filters),
                "items": [self._serialize_model(item) for item in records],
            }

        if action == "delete":
            if not self._scheduler_history_has_delete_scope(filters):
                raise ObjectToolError(
                    code="missing_delete_scope",
                    message="scheduler_history.delete requires at least one filter condition",
                    details={
                        "allowed_filters": [
                            "where.schedule_id",
                            "where.statuses",
                            "policy.retention_seconds",
                            "policy.keep_latest",
                            "object_id",
                        ]
                    },
                )
            query = self._build_scheduler_history_query(db, filters=filters)
            candidates = query.order_by(
                models.ScheduleRun.created_at.desc(), models.ScheduleRun.id.desc()
            ).all()
            keep_latest = filters["keep_latest"]
            if keep_latest is not None and keep_latest > 0:
                candidates = candidates[keep_latest:]
            sample = [self._serialize_model(item) for item in candidates[:10]]
            response = {
                "object_type": "scheduler_history",
                "action": "delete",
                "object_id": self._scheduler_history_audit_object_id(filters),
                "dry_run": filters["dry_run"],
                "filters": self._scheduler_history_filter_summary(filters),
                "candidate_count": len(candidates),
                "deleted_count": 0,
                "sample_runs": sample,
            }
            if filters["dry_run"]:
                return response
            for item in candidates:
                db.delete(item)
            response["deleted_count"] = len(candidates)
            return response

        raise ObjectToolError(code="invalid_action", message=f"Unsupported CRUD action: {action}")

    def _crud_knowledge(
        self,
        db: Session,
        *,
        object_type: str,
        action: str,
        object_id: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action not in ("list", "read"):
            raise ObjectToolError(
                code="invalid_action",
                message=f"{object_type} only supports list/read (use the Knowledge Base management page for admin operations)",
            )

        if object_type == "knowledge_base":
            if action == "list":
                kbs = db.query(models.KnowledgeBase).order_by(models.KnowledgeBase.id).all()
                items = []
                for kb in kbs:
                    doc_count = (
                        db.query(models.KnowledgeDocument)
                        .filter(models.KnowledgeDocument.kb_id == kb.id)
                        .count()
                    )
                    items.append(
                        {
                            "id": kb.id,
                            "name": kb.name,
                            "description": kb.description,
                            "tags": kb.tags or [],
                            "document_count": doc_count,
                            "doc_root": _knowledge_doc_root(kb.id),
                        }
                    )
                return {
                    "object_type": object_type,
                    "action": "list",
                    "count": len(items),
                    "items": items,
                }
            if action == "read":
                if object_id is None:
                    raise ObjectToolError(
                        code="missing_object_id", message="object_id is required for read"
                    )
                kb = (
                    db.query(models.KnowledgeBase)
                    .filter(models.KnowledgeBase.id == object_id)
                    .first()
                )
                if kb is None:
                    raise ObjectToolError(
                        code="not_found", message=f"knowledge_base {object_id} not found"
                    )
                doc_count = (
                    db.query(models.KnowledgeDocument)
                    .filter(models.KnowledgeDocument.kb_id == kb.id)
                    .count()
                )
                return {
                    "id": kb.id,
                    "name": kb.name,
                    "description": kb.description,
                    "tags": kb.tags or [],
                    "document_count": doc_count,
                    "doc_root": _knowledge_doc_root(kb.id),
                }

        if object_type == "knowledge_document":
            if action == "list":
                kb_id = payload.get("kb_id")
                if kb_id is None:
                    raise ObjectToolError(
                        code="invalid_payload",
                        message="knowledge_document.list requires payload.kb_id",
                    )
                docs = (
                    db.query(models.KnowledgeDocument)
                    .filter(models.KnowledgeDocument.kb_id == int(kb_id))
                    .order_by(models.KnowledgeDocument.filename)
                    .all()
                )
                return {
                    "object_type": object_type,
                    "action": "list",
                    "count": len(docs),
                    "items": [
                        {
                            "id": d.id,
                            "title": d.title,
                            "filename": d.filename,
                            "content_path": d.content_path,
                            "size_bytes": d.size_bytes,
                        }
                        for d in docs
                    ],
                }
            if action == "read":
                if object_id is None:
                    raise ObjectToolError(
                        code="missing_object_id", message="object_id is required for read"
                    )
                doc = (
                    db.query(models.KnowledgeDocument)
                    .filter(models.KnowledgeDocument.id == object_id)
                    .first()
                )
                if doc is None:
                    raise ObjectToolError(
                        code="not_found", message=f"knowledge_document {object_id} not found"
                    )
                return {
                    "id": doc.id,
                    "kb_id": doc.kb_id,
                    "title": doc.title,
                    "filename": doc.filename,
                    "content_path": doc.content_path,
                    "size_bytes": doc.size_bytes,
                }

        raise ObjectToolError(code="invalid_action", message=f"Unsupported action: {action}")

    def _crud_service(
        self,
        db: Session,
        *,
        action: str,
        object_id: int | None,
    ) -> dict[str, Any]:
        if action not in ("list", "read"):
            raise ObjectToolError(
                code="invalid_action",
                message="service only supports list/read",
            )

        def _serialize(svc: models.Service) -> dict[str, Any]:
            config = svc.config or {}
            safe_config = {k: v for k, v in config.items() if k != "password"}
            return {
                "id": svc.id,
                "name": svc.name,
                "service_type": svc.service_type,
                "config": safe_config,
                "resource_ref": svc.resource_ref,
                "status": svc.status,
            }

        if action == "list":
            services = db.query(models.Service).order_by(models.Service.id).all()
            return {
                "object_type": "service",
                "action": "list",
                "count": len(services),
                "items": [_serialize(s) for s in services],
            }

        if object_id is None:
            raise ObjectToolError(
                code="missing_object_id", message="object_id is required for read"
            )
        svc = db.query(models.Service).filter(models.Service.id == object_id).first()
        if svc is None:
            raise ObjectToolError(code="not_found", message=f"service {object_id} not found")
        return _serialize(svc)

    async def _operate_impl(
        self,
        db: Session,
        *,
        object_type: str,
        action: str,
        object_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if object_type == "page":
            page = db.query(models.Page).filter(models.Page.id == object_id).first()
            if page is None:
                raise ObjectToolError(code="not_found", message=f"page {object_id} not found")
            if action == "preview":
                self._page_lifecycle.transition(page, target_state=PageState.PREVIEWING)
            elif action == "publish":
                release = self._page_lifecycle.publish(
                    page,
                    artifact_payload=payload.get("artifact_payload") or page.draft_payload or {},
                    artifact_uri=payload.get("artifact_uri"),
                    release_notes=payload.get("release_notes"),
                )
                db.commit()
                db.refresh(page)
                db.refresh(release)
                return {
                    "object_type": "page",
                    "action": action,
                    "id": page.id,
                    "status": page.status,
                    "current_release_id": page.current_release_id,
                    "release": self._serialize_model(release),
                }
            elif action == "archive":
                self._page_lifecycle.archive(page)
            elif action == "rollback":
                release_id = payload.get("release_id")
                if not isinstance(release_id, int):
                    raise ObjectToolError(
                        code="missing_release_id",
                        message="rollback requires payload.release_id",
                    )
                self._page_lifecycle.rollback(page, target_release_id=release_id)
            else:
                raise ObjectToolError(
                    code="invalid_action", message=f"Unsupported page action: {action}"
                )
            db.commit()
            db.refresh(page)
            return self._serialize_model(page)

        if object_type == "function":
            function = db.query(models.Function).filter(models.Function.id == object_id).first()
            if function is None:
                raise ObjectToolError(code="not_found", message=f"function {object_id} not found")
            if action == "strategy":
                requirement_text = str(
                    payload.get("requirement") or function.description or function.name or ""
                )
                contract = (
                    payload.get("contract") if isinstance(payload.get("contract"), dict) else None
                )
                force_strategy = payload.get("force_strategy")
                thresholds = StrategyThresholds(
                    reuse=float(payload.get("reuse_threshold", 0.82)),
                    extend=float(payload.get("extend_threshold", 0.45)),
                )
                decision = self._function_strategy.decide(
                    db,
                    requirement_text=requirement_text,
                    contract=contract,
                    exclude_function_id=function.id,
                    force_strategy=force_strategy if isinstance(force_strategy, str) else None,
                    thresholds=thresholds,
                )
                return {
                    "object_type": "function",
                    "action": action,
                    "id": function.id,
                    **decision,
                }
            if action == "verify":
                code_snapshot = payload.get("code_snapshot") or function.draft_code or ""
                dependency_manifest = (
                    payload.get("dependency_manifest") or function.draft_dependencies
                )
                verification = self._function_verifier.verify_draft(
                    code_snapshot=code_snapshot,
                    dependency_manifest=dependency_manifest,
                )
                return {
                    "object_type": "function",
                    "action": action,
                    "id": function.id,
                    "verification": verification,
                }
            if action == "release":
                code_snapshot = payload.get("code_snapshot") or function.draft_code
                if not code_snapshot:
                    raise ObjectToolError(
                        code="missing_code_snapshot",
                        message="release requires code_snapshot or existing draft_code",
                    )
                requirement_text = str(
                    payload.get("requirement") or function.description or function.name or ""
                )
                contract = (
                    payload.get("contract") if isinstance(payload.get("contract"), dict) else None
                )
                strategy_decision = self._function_strategy.decide(
                    db,
                    requirement_text=requirement_text,
                    contract=contract,
                    exclude_function_id=function.id,
                    force_strategy=(
                        payload.get("force_strategy")
                        if isinstance(payload.get("force_strategy"), str)
                        else None
                    ),
                    thresholds=StrategyThresholds(
                        reuse=float(payload.get("reuse_threshold", 0.82)),
                        extend=float(payload.get("extend_threshold", 0.45)),
                    ),
                )
                verification = self._function_verifier.verify_draft(
                    code_snapshot=code_snapshot,
                    dependency_manifest=payload.get("dependency_manifest")
                    or function.draft_dependencies,
                )
                if not verification["passed"]:
                    raise ObjectToolError(
                        code="verification_failed",
                        message="Function verification failed before release",
                        details={
                            "diagnostics": verification["diagnostics"],
                            "checks": verification["checks"],
                            "strategy": strategy_decision["strategy"],
                        },
                    )
                release_metadata = payload.get("release_metadata")
                if release_metadata is None:
                    release_metadata = {}
                if not isinstance(release_metadata, dict):
                    raise ObjectToolError(
                        code="invalid_release_metadata",
                        message="release_metadata must be an object",
                    )
                enriched_metadata = {
                    **release_metadata,
                    "strategy_decision": strategy_decision,
                    "verification": verification,
                }
                release = self._function_lifecycle.release(
                    function,
                    code_snapshot=code_snapshot,
                    dependency_manifest=payload.get("dependency_manifest")
                    or function.draft_dependencies,
                    release_metadata=enriched_metadata,
                )
                db.commit()
                db.refresh(function)
                db.refresh(release)
                return {
                    "object_type": "function",
                    "action": action,
                    "id": function.id,
                    "status": function.status,
                    "current_release_id": function.current_release_id,
                    "strategy": strategy_decision["strategy"],
                    "verification_passed": verification["passed"],
                    "release": self._serialize_model(release),
                }
            if action == "invoke":
                runtime = FunctionRuntimeService(session_factory=self._session_factory)
                try:
                    trace_id = str(payload.get("trace_id") or uuid.uuid4())
                    result = await runtime.invoke(
                        function,
                        payload=payload.get("payload") or {},
                        datasource_id=payload.get("datasource_id"),
                        scope_metadata=payload.get("scope_metadata"),
                        timeout_seconds=float(payload.get("timeout_seconds", 30.0)),
                        trace_id=trace_id,
                    )
                finally:
                    runtime._executor.shutdown(cancel_futures=True)
                return {
                    "object_type": "function",
                    "action": action,
                    "id": function.id,
                    "trace_id": trace_id,
                    "run_id": result.run_id,
                    "status": result.status,
                    "error_class": result.error_class,
                    "error_message": result.error_message,
                    "output": result.output,
                    "duration_ms": result.duration_ms,
                }
            raise ObjectToolError(
                code="invalid_action", message=f"Unsupported function action: {action}"
            )

        if object_type == "scheduler":
            schedule = db.query(models.Schedule).filter(models.Schedule.id == object_id).first()
            if schedule is None:
                raise ObjectToolError(code="not_found", message=f"scheduler {object_id} not found")
            if action == "pause":
                self._schedule_lifecycle.pause(schedule)
                db.commit()
                db.refresh(schedule)
                return self._serialize_model(schedule)
            if action == "resume":
                self._schedule_lifecycle.resume(schedule)
                db.commit()
                db.refresh(schedule)
                return self._serialize_model(schedule)
            if action == "run-now":
                trace_id = str(payload.get("trace_id") or uuid.uuid4())
                worker = SchedulerWorker(
                    session_factory=self._session_factory,
                    runtime_service=FunctionRuntimeService(session_factory=self._session_factory),
                )
                try:
                    run_id = await worker.run_now(schedule.id, trace_id=trace_id)
                finally:
                    await worker.shutdown()
                return {
                    "object_type": "scheduler",
                    "action": action,
                    "id": schedule.id,
                    "trace_id": trace_id,
                    "run_id": run_id,
                }
            if action == "list-runs":
                limit = payload.get("limit", 20)
                if not isinstance(limit, int) or limit <= 0:
                    raise ObjectToolError(
                        code="invalid_limit", message="limit must be positive integer"
                    )
                runs = (
                    db.query(models.ScheduleRun)
                    .filter(models.ScheduleRun.schedule_id == schedule.id)
                    .order_by(models.ScheduleRun.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return {
                    "object_type": "scheduler",
                    "action": action,
                    "id": schedule.id,
                    "count": len(runs),
                    "runs": [self._serialize_model(run) for run in runs],
                }
            raise ObjectToolError(
                code="invalid_action", message=f"Unsupported scheduler action: {action}"
            )

        if object_type == "datasource":
            raise ObjectToolError(
                code="invalid_action",
                message=f"Datasource does not support operate action '{action}'",
            )

        if object_type == "channel":
            channel = db.query(models.Channel).filter(models.Channel.id == object_id).first()
            if channel is None:
                raise ObjectToolError(code="not_found", message=f"channel {object_id} not found")
            if action != "send":
                raise ObjectToolError(
                    code="invalid_action", message=f"Unsupported channel action: {action}"
                )
            try:
                result = await self._channel_delivery.send(channel=channel, payload=payload)
            except ChannelDeliveryError as err:
                raise ObjectToolError(
                    code=err.code,
                    message=err.message,
                    details=err.details,
                ) from err
            return {
                "object_type": "channel",
                "action": "send",
                "id": channel.id,
                "provider": channel.provider,
                "result": result,
            }

        raise ObjectToolError(
            code="invalid_object_type", message=f"Unsupported object type: {object_type}"
        )

    def _create_object(self, db: Session, object_type: str, payload: dict[str, Any]):
        if object_type == "page":
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ObjectToolError(
                    code="invalid_payload", message="page.create requires non-empty name"
                )
            return models.Page(
                name=name.strip(),
                description=payload.get("description"),
                status="draft",
                draft_payload=payload.get("draft_payload"),
            )

        if object_type == "function":
            self._reject_function_slug_payload(payload)
            name = normalize_function_display_name(payload.get("name"))
            self._validate_function_name_or_raise(name)
            return models.Function(
                name=name,
                slug=self._generate_function_slug(db, display_name=name),
                description=payload.get("description"),
                status="draft",
                draft_code=payload.get("draft_code"),
                draft_dependencies=payload.get("draft_dependencies"),
            )

        if object_type == "scheduler":
            target_type = (
                str(
                    payload.get("target_type")
                    or ("function" if payload.get("function_id") is not None else "function")
                )
                .strip()
                .lower()
            )
            raw_target_id = payload.get("target_id", payload.get("function_id"))
            if target_type not in {"function", "agent"}:
                raise ObjectToolError(
                    code="invalid_payload",
                    message="scheduler.create requires target_type=function|agent",
                )
            if not isinstance(raw_target_id, int):
                raise ObjectToolError(
                    code="invalid_payload",
                    message="scheduler.create requires integer target_id",
                )

            function_id: int | None = None
            function_release_id: int | None = None
            if target_type == "function":
                function = (
                    db.query(models.Function).filter(models.Function.id == raw_target_id).first()
                )
                if function is None:
                    raise ObjectToolError(
                        code="not_found", message=f"function {raw_target_id} not found"
                    )
                self._function_lifecycle.ensure_released_target(function)
                function_id = function.id
                function_release_id = function.current_release_id
            else:
                agent = db.query(models.Agent).filter(models.Agent.id == raw_target_id).first()
                if agent is None:
                    raise ObjectToolError(
                        code="not_found", message=f"agent {raw_target_id} not found"
                    )
                if str(agent.status or "").strip().lower() != "active":
                    raise ObjectToolError(
                        code="validation_error", message="agent must be active before scheduling"
                    )

            schedule_type = payload.get("schedule_type", "cron")
            cron_expression = payload.get("cron_expression")
            interval_seconds = payload.get("interval_seconds")
            input_prompt = str(payload.get("input_prompt") or "").strip() or None
            self._schedule_lifecycle.validate_definition(
                schedule_type=schedule_type,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
            )
            status = str(payload.get("status") or "active").strip().lower()
            if status not in {"active", "paused"}:
                raise ObjectToolError(
                    code="validation_error", message="scheduler status must be active or paused"
                )
            next_run_at = None
            if status == "active":
                next_run_at = self._schedule_lifecycle.calculate_next_run_at(
                    schedule_type=schedule_type,
                    cron_expression=cron_expression,
                    interval_seconds=interval_seconds,
                )
            return models.Schedule(
                name=str(payload.get("name") or f"schedule-{target_type}-{raw_target_id}"),
                status=status,
                target_type=target_type,
                target_id=raw_target_id,
                schedule_type=schedule_type,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
                timezone=str(payload.get("timezone") or "UTC"),
                function_id=function_id,
                function_release_id=function_release_id,
                input_payload=payload.get("input_payload")
                if isinstance(payload.get("input_payload"), dict)
                else None,
                input_prompt=input_prompt,
                next_run_at=next_run_at,
                max_retries=int(payload.get("max_retries", 0)),
                retry_backoff_seconds=int(payload.get("retry_backoff_seconds", 60)),
            )

        if object_type == "channel":
            raw_name = payload.get("name")
            name = str(raw_name or "").strip()
            if not name:
                raise ObjectToolError(
                    code="invalid_payload", message="channel.create requires non-empty name"
                )
            try:
                provider = normalize_channel_provider(payload.get("provider", "dingtalk"))
                status = normalize_channel_status(payload.get("status", "active"))
                config = self._compose_channel_config(payload)
                normalized_config = normalize_channel_config(provider=provider, config=config)
            except ChannelDeliveryError as err:
                raise ObjectToolError(
                    code=err.code, message=err.message, details=err.details
                ) from err
            return models.Channel(
                name=name,
                provider=provider,
                description=payload.get("description"),
                status=status,
                config=normalized_config,
            )

        if object_type == "datasource":
            required = ["name", "host", "port", "user", "password", "database"]
            missing = [key for key in required if key not in payload]
            if missing:
                raise ObjectToolError(
                    code="invalid_payload",
                    message="datasource.create missing required fields",
                    details={"missing": missing},
                )
            cluster_key = payload.get("cluster_key") or f"{payload['host']}:{payload['port']}"
            return models.DataSource(
                name=payload["name"],
                host=payload["host"],
                port=int(payload["port"]),
                db_type=str(payload.get("db_type", "mysql")),
                cluster_key=cluster_key,
                tenant_role=str(payload.get("tenant_role", "user")),
                attributes=payload.get("attributes")
                if isinstance(payload.get("attributes"), dict)
                else None,
                user=payload["user"],
                password=payload["password"],
                database=payload["database"],
                status=str(payload.get("status", "active")),
            )

        raise ObjectToolError(
            code="invalid_object_type", message=f"Unsupported object type: {object_type}"
        )

    def _update_object(
        self,
        db: Session,
        object_type: str,
        item: Any,
        payload: dict[str, Any],
    ) -> None:
        if object_type == "page":
            allowed = {"name", "description", "draft_payload"}
        elif object_type == "function":
            allowed = {"name", "description", "draft_code", "draft_dependencies"}
        elif object_type == "scheduler":
            allowed = {
                "name",
                "cron_expression",
                "interval_seconds",
                "schedule_type",
                "timezone",
                "max_retries",
                "retry_backoff_seconds",
                "input_payload",
                "input_prompt",
                "status",
            }
        elif object_type == "channel":
            allowed = {
                "name",
                "provider",
                "description",
                "status",
                "config",
                "webhook_url",
                "security",
                "template",
            }
        elif object_type == "datasource":
            allowed = {
                "name",
                "host",
                "port",
                "db_type",
                "cluster_key",
                "tenant_role",
                "attributes",
                "user",
                "password",
                "database",
                "status",
            }
        else:
            raise ObjectToolError(
                code="invalid_object_type", message=f"Unsupported object type: {object_type}"
            )

        for field, value in payload.items():
            if field in allowed and hasattr(item, field):
                if object_type == "function" and field == "slug":
                    continue
                if object_type == "function" and field == "name":
                    next_name = normalize_function_display_name(value)
                    self._validate_function_name_or_raise(next_name)
                    setattr(item, field, next_name)
                    continue
                setattr(item, field, value)

        if object_type == "function":
            self._reject_function_slug_payload(payload)

        if object_type == "scheduler":
            if "target_type" in payload or "target_id" in payload or "function_id" in payload:
                target_type = (
                    str(
                        payload.get("target_type")
                        or (
                            "function"
                            if payload.get("function_id") is not None
                            else item.target_type
                        )
                    )
                    .strip()
                    .lower()
                )
                raw_target_id = payload.get("target_id", payload.get("function_id", item.target_id))
                if target_type not in {"function", "agent"} or not isinstance(raw_target_id, int):
                    raise ObjectToolError(
                        code="invalid_payload", message="scheduler target update is invalid"
                    )
                item.target_type = target_type
                item.target_id = raw_target_id
                if target_type == "function":
                    function = (
                        db.query(models.Function)
                        .filter(models.Function.id == raw_target_id)
                        .first()
                    )
                    if function is None:
                        raise ObjectToolError(
                            code="not_found", message=f"function {raw_target_id} not found"
                        )
                    self._function_lifecycle.ensure_released_target(function)
                    item.function_id = function.id
                    item.function_release_id = function.current_release_id
                else:
                    agent = db.query(models.Agent).filter(models.Agent.id == raw_target_id).first()
                    if agent is None:
                        raise ObjectToolError(
                            code="not_found", message=f"agent {raw_target_id} not found"
                        )
                    if str(agent.status or "").strip().lower() != "active":
                        raise ObjectToolError(
                            code="validation_error",
                            message="agent must be active before scheduling",
                        )
                    item.function_id = None
                    item.function_release_id = None
            if "input_prompt" in payload:
                item.input_prompt = str(payload.get("input_prompt") or "").strip() or None
            self._schedule_lifecycle.validate_definition(
                schedule_type=item.schedule_type,
                cron_expression=item.cron_expression,
                interval_seconds=item.interval_seconds,
            )
            if item.status == "active":
                item.next_run_at = self._schedule_lifecycle.calculate_next_run_at(
                    schedule_type=item.schedule_type,
                    cron_expression=item.cron_expression,
                    interval_seconds=item.interval_seconds,
                )
            else:
                item.next_run_at = None

        if object_type == "channel":
            try:
                if "provider" in payload:
                    item.provider = normalize_channel_provider(payload.get("provider"))
                if "status" in payload:
                    item.status = normalize_channel_status(payload.get("status"))
                merged_config = self._compose_channel_config(payload, current=item.config)
                item.config = normalize_channel_config(provider=item.provider, config=merged_config)
            except ChannelDeliveryError as err:
                raise ObjectToolError(
                    code=err.code, message=err.message, details=err.details
                ) from err

    def _resolve_model(self, object_type: str):
        mapping = {
            "page": models.Page,
            "function": models.Function,
            "scheduler": models.Schedule,
            "datasource": models.DataSource,
            "channel": models.Channel,
            "knowledge_base": models.KnowledgeBase,
            "knowledge_document": models.KnowledgeDocument,
            "service": models.Service,
        }
        model_cls = mapping.get(object_type)
        if model_cls is None:
            raise ObjectToolError(
                code="invalid_object_type", message=f"Unsupported object type: {object_type}"
            )
        return model_cls

    def _compose_channel_config(
        self,
        payload: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
        raw_config = payload.get("config")
        if isinstance(raw_config, dict):
            config = dict(raw_config)
        if "webhook_url" in payload:
            config["webhook_url"] = str(payload.get("webhook_url") or "").strip()
        if "security" in payload and isinstance(payload.get("security"), dict):
            config["security"] = payload.get("security")
        if "template" in payload and isinstance(payload.get("template"), dict):
            config["template"] = payload.get("template")
        return config

    def _validate_function_name_or_raise(self, name: str) -> None:
        detail = validate_function_display_name(name)
        if detail is not None:
            raise ObjectToolError(code="invalid_payload", message=detail)

    def _reject_function_slug_payload(self, payload: dict[str, Any]) -> None:
        if "slug" in payload:
            raise ObjectToolError(
                code="invalid_payload",
                message="function slug is managed by the system",
            )

    def _function_slug_exists(
        self,
        db: Session,
        *,
        slug: str,
        exclude_function_id: int | None = None,
    ) -> bool:
        query = db.query(models.Function).filter(models.Function.slug == slug)
        if exclude_function_id is not None:
            query = query.filter(models.Function.id != exclude_function_id)
        return query.first() is not None

    def _generate_function_slug(
        self,
        db: Session,
        *,
        display_name: str,
        exclude_function_id: int | None = None,
    ) -> str:
        return generate_unique_function_slug(
            display_name,
            exists=lambda candidate: self._function_slug_exists(
                db,
                slug=normalize_function_slug(candidate),
                exclude_function_id=exclude_function_id,
            ),
        )

    def _normalize_object_type(self, object_type: str) -> str:
        normalized = str(object_type or "").strip().lower()
        if normalized not in self.allowed_object_types:
            raise ObjectToolError(
                code="invalid_object_type",
                message=f"Unsupported object_type '{object_type}'",
                details={"allowed": sorted(self.allowed_object_types)},
            )
        return normalized

    def _normalize_scheduler_history_filters(
        self,
        payload: dict[str, Any],
        *,
        object_id: int | None,
    ) -> dict[str, Any]:
        raw_limit = payload.get("limit", 20)
        if not isinstance(raw_limit, int) or raw_limit <= 0:
            raise ObjectToolError(code="invalid_limit", message="limit must be positive integer")

        where = payload.get("where")
        if where is None:
            where = {}
        if not isinstance(where, dict):
            raise ObjectToolError(code="invalid_payload", message="where must be object")

        policy = payload.get("policy")
        if policy is None:
            policy = {}
        if not isinstance(policy, dict):
            raise ObjectToolError(code="invalid_payload", message="policy must be object")

        schedule_id = where.get("schedule_id")
        if schedule_id is not None:
            if not isinstance(schedule_id, int) or schedule_id <= 0:
                raise ObjectToolError(
                    code="invalid_payload", message="where.schedule_id must be positive integer"
                )

        raw_statuses = where.get("statuses")
        statuses: list[str] | None = None
        if raw_statuses is not None:
            if not isinstance(raw_statuses, list) or not raw_statuses:
                raise ObjectToolError(
                    code="invalid_payload", message="where.statuses must be a non-empty string list"
                )
            normalized_statuses = [
                str(item or "").strip().lower() for item in raw_statuses if str(item or "").strip()
            ]
            if not normalized_statuses:
                raise ObjectToolError(
                    code="invalid_payload", message="where.statuses must be a non-empty string list"
                )
            unknown_statuses = sorted(
                {item for item in normalized_statuses if item not in SCHEDULER_HISTORY_STATUS_ENUM}
            )
            if unknown_statuses:
                raise ObjectToolError(
                    code="invalid_payload",
                    message=f"where.statuses contains unsupported values: {', '.join(unknown_statuses)}",
                )
            statuses = normalized_statuses

        retention_seconds = policy.get("retention_seconds")
        if retention_seconds is not None:
            if not isinstance(retention_seconds, int) or retention_seconds <= 0:
                raise ObjectToolError(
                    code="invalid_payload",
                    message="policy.retention_seconds must be positive integer",
                )

        keep_latest = policy.get("keep_latest")
        if keep_latest is not None:
            if not isinstance(keep_latest, int) or keep_latest < 0:
                raise ObjectToolError(
                    code="invalid_payload",
                    message="policy.keep_latest must be non-negative integer",
                )

        unknown_where_keys = sorted(set(where.keys()) - {"schedule_id", "statuses"})
        if unknown_where_keys:
            raise ObjectToolError(
                code="invalid_payload",
                message=f"where contains undeclared fields: {', '.join(unknown_where_keys)}",
            )
        unknown_policy_keys = sorted(set(policy.keys()) - {"retention_seconds", "keep_latest"})
        if unknown_policy_keys:
            raise ObjectToolError(
                code="invalid_payload",
                message=f"policy contains undeclared fields: {', '.join(unknown_policy_keys)}",
            )

        dry_run = bool(payload.get("dry_run"))
        return {
            "object_id": int(object_id) if object_id is not None else None,
            "schedule_id": schedule_id,
            "retention_seconds": retention_seconds,
            "keep_latest": keep_latest,
            "statuses": statuses,
            "dry_run": dry_run,
            "limit": min(raw_limit, 500),
        }

    def _build_scheduler_history_query(
        self,
        db: Session,
        *,
        filters: dict[str, Any],
    ):
        query = db.query(models.ScheduleRun)
        if filters["object_id"] is not None:
            query = query.filter(models.ScheduleRun.id == filters["object_id"])
        if filters["schedule_id"] is not None:
            query = query.filter(models.ScheduleRun.schedule_id == filters["schedule_id"])
        if filters["statuses"]:
            query = query.filter(models.ScheduleRun.status.in_(filters["statuses"]))
        if filters["retention_seconds"] is not None:
            cutoff = datetime.utcnow() - timedelta(seconds=int(filters["retention_seconds"]))
            query = query.filter(models.ScheduleRun.created_at < cutoff)
        return query

    def _scheduler_history_has_delete_scope(self, filters: dict[str, Any]) -> bool:
        return any(
            (
                filters["object_id"] is not None,
                filters["schedule_id"] is not None,
                filters["retention_seconds"] is not None,
                filters["keep_latest"] is not None,
                bool(filters["statuses"]),
            )
        )

    def _scheduler_history_filter_summary(self, filters: dict[str, Any]) -> dict[str, Any]:
        return {
            "object_id": filters["object_id"],
            "schedule_id": filters["schedule_id"],
            "retention_seconds": filters["retention_seconds"],
            "keep_latest": filters["keep_latest"],
            "statuses": filters["statuses"],
        }

    def _scheduler_history_audit_object_id(self, filters: dict[str, Any]) -> str:
        if filters["object_id"] is not None:
            return str(filters["object_id"])
        if filters["schedule_id"] is not None:
            return f"schedule:{filters['schedule_id']}"
        return "bulk"

    def _enforce_policy(self, *, action: str, actor: str) -> None:
        normalized_actor = str(actor or "").strip()
        if not normalized_actor:
            raise ObjectToolError(
                code="policy_violation",
                message="actor is required for object tool actions",
                details={"action": action},
            )
        if action in self.sensitive_actions and normalized_actor == "unknown":
            raise ObjectToolError(
                code="policy_violation",
                message=f"actor '{normalized_actor}' is not allowed for sensitive action {action}",
                details={"action": action, "actor": normalized_actor},
            )

    def _write_audit(
        self,
        db: Session,
        *,
        object_type: str,
        object_id: str,
        action: str,
        actor: str,
        result: str,
        detail: dict[str, Any],
    ) -> None:
        entry = models.ObjectAuditLog(
            object_type=object_type,
            object_id=object_id,
            action=action,
            actor=actor or "llm",
            result=result,
            detail=detail,
            created_at=datetime.utcnow(),
        )
        db.add(entry)

    def _serialize_model(self, record: Any) -> dict[str, Any]:
        raw = {column.name: getattr(record, column.name) for column in record.__table__.columns}
        return json.loads(json.dumps(raw, default=str, ensure_ascii=False))

    def _summarize(self, payload: Any, limit: int = 600) -> str:
        text = json.dumps(payload, default=str, ensure_ascii=False)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...<truncated>"
