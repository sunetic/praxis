"""Immutable Page source, compilation evidence, and guarded publication."""

import time
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from app.models.artifacts import (
    artifact_validations,
    function_revisions,
    page_compilations,
    page_owned_functions,
    page_revisions,
)
from app.models.models import Function, FunctionRelease, Page, PageRelease
from app.services.agent.store import fingerprint
from app.services.function.identity import generate_unique_function_slug
from app.services.function.native_authoring import AuthoringError
from app.services.page.contracts import PageSource


class PageAuthoringStore:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _get(db, page_id, *, lock=False):
        if lock:
            db.execute(
                update(Page)
                .where(Page.id == page_id)
                .values(updated_at=datetime.now(UTC).replace(tzinfo=None))
                .execution_options(synchronize_session=False)
            )
        page = db.get(Page, page_id, populate_existing=lock)
        if page is None:
            raise AuthoringError("Page not found")
        return page

    @staticmethod
    def _source(page):
        return PageSource.model_validate(
            page.draft_payload or {"files": {}, "bindings": {}}
        ).model_dump(mode="json")

    @staticmethod
    def _changed(previous, current):
        changed = [
            name
            for name in sorted(previous["files"].keys() | current["files"].keys())
            if previous["files"].get(name) != current["files"].get(name)
        ]
        return changed

    @classmethod
    def _revision(cls, db, page):
        row = (
            db.execute(
                select(page_revisions)
                .where(page_revisions.c.page_id == page.id)
                .order_by(page_revisions.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return row if row and row["revision_hash"] == fingerprint(cls._source(page)) else None

    def read(self, page_id):
        with self.sessions() as db:
            page = self._get(db, page_id)
            source = self._source(page)
            revision = self._revision(db, page)
            validation = None
            compiled = None
            if revision:
                validation = (
                    db.execute(
                        select(artifact_validations)
                        .where(
                            artifact_validations.c.object_type == "page",
                            artifact_validations.c.object_id == page_id,
                            artifact_validations.c.revision_id == revision["id"],
                        )
                        .order_by(artifact_validations.c.created_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if validation:
                    compiled = (
                        db.execute(
                            select(page_compilations).where(
                                page_compilations.c.validation_id == validation["id"]
                            )
                        )
                        .mappings()
                        .first()
                    )
            owned = (
                db.execute(
                    select(Function.id, Function.name, Function.current_release_id)
                    .join(page_owned_functions, page_owned_functions.c.function_id == Function.id)
                    .where(page_owned_functions.c.page_id == page_id)
                )
                .mappings()
                .all()
            )
            previous = (
                db.execute(
                    select(page_revisions)
                    .where(
                        page_revisions.c.page_id == page_id,
                        page_revisions.c.id != revision["id"],
                    )
                    .order_by(page_revisions.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
                if revision
                else None
            )
            release = (
                db.get(PageRelease, page.current_release_id) if page.current_release_id else None
            )
            return {
                "page_id": page_id,
                "name": page.name,
                **source,
                "revision_hash": fingerprint(source),
                "revision_id": revision["id"] if revision else None,
                "current_release_id": page.current_release_id,
                "released_revision_id": (release.artifact_payload or {}).get("revision_id")
                if release
                else None,
                "changed_files": self._changed(previous or {"files": {}, "bindings": {}}, source),
                "bindings_changed": (previous["bindings"] if previous else {})
                != source["bindings"],
                "validation": dict(validation) if validation else None,
                "artifact_hash": compiled["artifact_hash"] if compiled else None,
                "owned_functions": [dict(item) for item in owned],
            }

    def write(self, page_id, *, expected_revision, source: PageSource, run_id):
        values = source.model_dump(mode="json")
        with self.sessions.begin() as db:
            page = self._get(db, page_id, lock=True)
            previous = self._source(page)
            if fingerprint(previous) != expected_revision:
                raise AuthoringError("Page draft changed; read the current revision before editing")
            changed = self._changed(previous, values)
            row = dict(
                id=uuid.uuid4().hex,
                page_id=page_id,
                revision_hash=fingerprint(values),
                **values,
                run_id=run_id,
                created_at=time.time(),
            )
            db.execute(insert(page_revisions).values(**row))
            page.draft_payload = values
            return {
                "page_id": page_id,
                "revision_id": row["id"],
                "revision_hash": row["revision_hash"],
                "changed_files": changed,
                "bindings_changed": previous["bindings"] != values["bindings"],
                "validation": None,
                "current_release_id": page.current_release_id,
            }

    @staticmethod
    def binding_checks(db, bindings):
        diagnostics = []
        for name, raw in bindings.items():
            binding = raw if isinstance(raw, dict) else raw.model_dump()
            function = db.get(Function, binding["function_id"])
            if function is None:
                diagnostics.append(f"{name}: Function not found")
                continue
            if binding.get("release_id"):
                release = db.get(FunctionRelease, binding["release_id"])
                metadata = (release.release_metadata or {}) if release else {}
                validation = (
                    db.execute(
                        select(artifact_validations).where(
                            artifact_validations.c.id == metadata.get("validation_id")
                        )
                    )
                    .mappings()
                    .first()
                    if metadata.get("validation_id")
                    else None
                )
                revision = (
                    db.execute(
                        select(function_revisions).where(
                            function_revisions.c.id == metadata.get("revision_id")
                        )
                    )
                    .mappings()
                    .first()
                    if metadata.get("revision_id")
                    else None
                )
                valid = (
                    release
                    and release.function_id == function.id
                    and revision
                    and revision["function_id"] == function.id
                    and validation
                    and validation["object_type"] == "function"
                    and validation["object_id"] == function.id
                    and validation["revision_id"] == revision["id"]
                    and validation["revision_hash"] == revision["revision_hash"]
                    and release.code_snapshot == revision["code"]
                    and release.dependency_manifest == revision["dependencies"]
                    and {item["name"] for item in validation["checks"]}
                    >= {"python_syntax", "entrypoint", "controlled_runtime"}
                    and all(
                        item.get("executed") is True and item.get("status") == "passed"
                        for item in validation["checks"]
                    )
                )
                if not valid:
                    diagnostics.append(
                        f"{name}: exact Function release has no matching executed validation"
                    )
            else:
                revision = (
                    db.execute(
                        select(function_revisions).where(
                            function_revisions.c.id == binding.get("revision_id"),
                            function_revisions.c.function_id == function.id,
                        )
                    )
                    .mappings()
                    .first()
                )
                diagnostics.append(
                    f"{name}: Function draft is not a published dependency"
                    if revision
                    else f"{name}: Function revision not found"
                )
        return {
            "name": "function_bindings",
            "executed": True,
            "status": "failed" if diagnostics else "passed",
            "diagnostic": "; ".join(diagnostics),
            "bindings": bindings,
        }

    def inspect_bindings(self, bindings):
        with self.sessions() as db:
            return self.binding_checks(db, bindings)

    def record_validation(self, page_id, *, revision_id, revision_hash, checks, html, run_id):
        with self.sessions.begin() as db:
            page = self._get(db, page_id, lock=True)
            revision = self._revision(db, page)
            if (
                not revision
                or revision["id"] != revision_id
                or revision["revision_hash"] != revision_hash
            ):
                raise AuthoringError(
                    "Page draft changed during validation; results cannot validate a new revision"
                )
            report = dict(
                id=uuid.uuid4().hex,
                object_type="page",
                object_id=page_id,
                revision_id=revision_id,
                revision_hash=revision_hash,
                checks=checks,
                run_id=run_id,
                created_at=time.time(),
            )
            db.execute(insert(artifact_validations).values(**report))
            if html is not None:
                db.execute(
                    insert(page_compilations).values(
                        validation_id=report["id"], html=html, artifact_hash=fingerprint(html)
                    )
                )
            return report

    def preview(self, page_id, *, revision_id):
        with self.sessions() as db:
            page = self._get(db, page_id)
            current = self._revision(db, page)
            if not current or current["id"] != revision_id:
                raise AuthoringError("Preview requires the current exact saved revision")
            row = (
                db.execute(
                    select(page_compilations)
                    .join(
                        artifact_validations,
                        page_compilations.c.validation_id == artifact_validations.c.id,
                    )
                    .where(
                        artifact_validations.c.object_type == "page",
                        artifact_validations.c.object_id == page_id,
                        artifact_validations.c.revision_id == revision_id,
                    )
                    .order_by(artifact_validations.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                raise AuthoringError("Compile the current Page revision before previewing it")
            return {"page_id": page_id, "revision_id": revision_id, **dict(row)}

    def publish(self, page_id, *, expected_revision, validation_id, db=None):
        with nullcontext(db) if db is not None else self.sessions.begin() as db:
            page = self._get(db, page_id, lock=True)
            if page.status == "archived":
                raise AuthoringError("Archived Pages cannot be published")
            revision = self._revision(db, page)
            report = (
                db.execute(
                    select(artifact_validations).where(
                        artifact_validations.c.id == validation_id,
                        artifact_validations.c.object_type == "page",
                        artifact_validations.c.object_id == page_id,
                    )
                )
                .mappings()
                .first()
            )
            compiled = (
                db.execute(
                    select(page_compilations).where(
                        page_compilations.c.validation_id == validation_id
                    )
                )
                .mappings()
                .first()
            )
            if (
                not revision
                or revision["revision_hash"] != expected_revision
                or not report
                or report["revision_id"] != revision["id"]
                or report["revision_hash"] != expected_revision
                or not compiled
            ):
                raise AuthoringError(
                    "Publication requires compilation and checks of the current exact Page revision"
                )
            required = {"source_compile", "function_bindings", "browser_runtime"}
            if revision["bindings"]:
                required.add("binding_runtime")
            if not required <= {item["name"] for item in report["checks"]} or any(
                item.get("executed") is not True or item.get("status") != "passed"
                for item in report["checks"]
                # Applicability comes from the immutable source, never a
                # client/model-provided 'applicable' or 'passed' declaration.
                if item["name"] != "binding_runtime" or revision["bindings"]
            ):
                raise AuthoringError(
                    "Required Page checks have not passed; draft remains unpublished"
                )
            if self.binding_checks(db, revision["bindings"])["status"] != "passed":
                raise AuthoringError(
                    "Function dependency evidence changed; validate before publication"
                )
            previous = (
                db.get(PageRelease, page.current_release_id) if page.current_release_id else None
            )
            if previous and (previous.artifact_payload or {}).get("revision_id") == revision["id"]:
                return {"page_id": page_id, "release_id": previous.id, "already_published": True}
            version = (
                db.execute(
                    select(PageRelease.version)
                    .where(PageRelease.page_id == page_id)
                    .order_by(PageRelease.version.desc())
                    .limit(1)
                ).scalar()
                or 0
            )
            release = PageRelease(
                page_id=page_id,
                version=version + 1,
                artifact_payload={
                    "revision_id": revision["id"],
                    "revision_hash": expected_revision,
                    "validation_id": validation_id,
                    "checks": report["checks"],
                    "files": revision["files"],
                    "bindings": revision["bindings"],
                    "html": compiled["html"],
                    "artifact_hash": compiled["artifact_hash"],
                },
            )
            db.add(release)
            db.flush()
            page.current_release_id = release.id
            page.status = "published"
            return {
                "page_id": page_id,
                "release_id": release.id,
                "revision_hash": expected_revision,
                "already_published": False,
            }

    def create_function(self, page_id, *, name, description):
        with self.sessions.begin() as db:
            self._get(db, page_id, lock=True)
            function = Function(
                name=name,
                description=description,
                kind="custom",
                status="draft",
                draft_code="",
                draft_dependencies={},
                slug=generate_unique_function_slug(
                    name,
                    exists=lambda slug: (
                        db.query(Function.id).filter(Function.slug == slug).first() is not None
                    ),
                ),
            )
            db.add(function)
            db.flush()
            db.execute(
                insert(page_owned_functions).values(page_id=page_id, function_id=function.id)
            )
            return {
                "page_id": page_id,
                "function_id": function.id,
                "name": function.name,
                "status": "draft",
                "current_release_id": None,
            }
