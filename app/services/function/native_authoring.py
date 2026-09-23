"""Function draft revisions and publication. No model calls or build orchestration."""

import ast
import json
import time
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from app.models.artifacts import artifact_validations, function_revisions
from app.models.models import Function, FunctionRelease
from app.services.agent.store import fingerprint


class AuthoringError(ValueError):
    pass


def revision_hash(code: str, dependencies: dict) -> str:
    return fingerprint({"main.py": code, "dependencies": dependencies})


def static_checks(code: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [
            {
                "name": "python_syntax",
                "status": "failed",
                "executed": True,
                "diagnostic": f"{exc.msg} at line {exc.lineno}",
            },
            {"name": "entrypoint", "status": "not_run", "executed": False},
        ]
    main = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "FunctionBase" for base in node.bases)
    ]
    runner = next(
        (
            member
            for cls in classes
            for member in cls.body
            if isinstance(member, ast.FunctionDef) and member.name == "run"
        ),
        None,
    )
    entry = main or runner
    expected = 2 if main is not None else 3
    valid = (
        entry is not None
        and len(entry.args.posonlyargs + entry.args.args) == expected
        and not entry.args.kwonlyargs
    )
    return [
        {"name": "python_syntax", "status": "passed", "executed": True},
        {
            "name": "entrypoint",
            "status": "passed" if valid else "failed",
            "executed": True,
            "diagnostic": "Requires main(payload, context) or FunctionBase.run(self, payload, context).",
        },
    ]


class FunctionAuthoringStore:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _get(db, function_id):
        function = db.get(Function, function_id)
        if function is None:
            raise AuthoringError("Function not found")
        return function

    @staticmethod
    def _lock(db, function_id):
        # All native edits and releases acquire this row before reading the
        # draft. The hash comparison then applies to the serialized current row.
        count = db.execute(
            update(Function)
            .where(Function.id == function_id)
            .values(updated_at=datetime.now(UTC).replace(tzinfo=None))
            .execution_options(synchronize_session=False)
        ).rowcount
        if count != 1:
            raise AuthoringError("Function not found")
        function = db.get(Function, function_id, populate_existing=True)
        if function.kind in {"built_in", "builtin"}:
            raise AuthoringError("Built-in Functions cannot be edited or published here")
        return function

    @staticmethod
    def _revision(db, function):
        code, dependencies = function.draft_code or "", function.draft_dependencies or {}
        current_hash = revision_hash(code, dependencies)
        row = (
            db.execute(
                select(function_revisions)
                .where(function_revisions.c.function_id == function.id)
                .order_by(function_revisions.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        # An out-of-band edit must never inherit a previous validation record.
        return row if row and row["revision_hash"] == current_hash else None

    def read(self, function_id):
        with self.sessions() as db:
            function = self._get(db, function_id)
            code, dependencies = function.draft_code or "", function.draft_dependencies or {}
            revision = self._revision(db, function)
            check = None
            if revision:
                check = (
                    db.execute(
                        select(artifact_validations)
                        .where(
                            artifact_validations.c.object_type == "function",
                            artifact_validations.c.object_id == function_id,
                            artifact_validations.c.revision_id == revision["id"],
                        )
                        .order_by(artifact_validations.c.created_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
            previous = (
                db.execute(
                    select(function_revisions)
                    .where(
                        function_revisions.c.function_id == function_id,
                        function_revisions.c.created_at < revision["created_at"],
                    )
                    .order_by(function_revisions.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
                if revision
                else None
            )
            changed_files = []
            if revision:
                if code != (previous["code"] if previous else ""):
                    changed_files.append("main.py")
                if dependencies != (previous["dependencies"] if previous else {}):
                    changed_files.append("manifest.json")
            release = (
                db.get(FunctionRelease, function.current_release_id)
                if function.current_release_id
                else None
            )
            return {
                "function_id": function.id,
                "name": function.name,
                "slug": function.slug,
                "code": code,
                "dependencies": dependencies,
                "revision_hash": revision_hash(code, dependencies),
                "revision_id": revision["id"] if revision else None,
                "current_release_id": function.current_release_id,
                "released_revision_id": (release.release_metadata or {}).get("revision_id")
                if release
                else None,
                "changed_files": changed_files,
                "validation": dict(check) if check else None,
            }

    def write(self, function_id, *, expected_revision, code, dependencies, run_id):
        with self.sessions.begin() as db:
            function = self._lock(db, function_id)
            if (
                revision_hash(function.draft_code or "", function.draft_dependencies or {})
                != expected_revision
            ):
                raise AuthoringError("Draft changed; read the current revision before editing")
            # JSON serialization also rejects non-JSON/NaN values before a save.
            json.dumps(dependencies, allow_nan=False)
            changed_files = []
            if code != (function.draft_code or ""):
                changed_files.append("main.py")
            if dependencies != (function.draft_dependencies or {}):
                changed_files.append("manifest.json")
            digest = revision_hash(code, dependencies)
            revision_id = uuid.uuid4().hex
            db.execute(
                insert(function_revisions).values(
                    id=revision_id,
                    function_id=function_id,
                    revision_hash=digest,
                    code=code,
                    dependencies=dependencies,
                    run_id=run_id,
                    created_at=time.time(),
                )
            )
            function.draft_code = code
            function.draft_dependencies = dependencies
            # Editing a draft does not remove or alter an existing immutable release.
            return {
                "function_id": function_id,
                "revision_id": revision_id,
                "revision_hash": digest,
                "changed_files": changed_files,
                "validation": None,
                "current_release_id": function.current_release_id,
            }

    def record_validation(self, function_id, *, revision_id, revision_hash, checks, run_id):
        with self.sessions.begin() as db:
            function = self._lock(db, function_id)
            current = self._revision(db, function)
            if (
                current is None
                or current["id"] != revision_id
                or current["revision_hash"] != revision_hash
            ):
                raise AuthoringError(
                    "Draft changed during validation; results do not apply to the current version"
                )
            report = dict(
                id=uuid.uuid4().hex,
                object_type="function",
                object_id=function_id,
                revision_id=revision_id,
                revision_hash=revision_hash,
                checks=checks,
                run_id=run_id,
                created_at=time.time(),
            )
            db.execute(insert(artifact_validations).values(**report))
            return report

    def publish(self, function_id, *, expected_revision, validation_id, db=None):
        # Domain callers already in a transaction use the same row lock and
        # guard. They own commit/rollback; never nest a second DB transaction.
        with nullcontext(db) if db is not None else self.sessions.begin() as db:
            function = self._lock(db, function_id)
            current = self._revision(db, function)
            report = (
                db.execute(
                    select(artifact_validations).where(
                        artifact_validations.c.id == validation_id,
                        artifact_validations.c.object_type == "function",
                        artifact_validations.c.object_id == function_id,
                    )
                )
                .mappings()
                .first()
            )
            if (
                not current
                or current["revision_hash"] != expected_revision
                or not report
                or report["revision_id"] != current["id"]
                or report["revision_hash"] != expected_revision
            ):
                raise AuthoringError(
                    "Publication requires validation of the current exact draft revision"
                )
            required = {"python_syntax", "entrypoint", "controlled_runtime"}
            checks = {item["name"]: item for item in report["checks"]}
            if not required <= checks.keys() or any(
                item.get("status") != "passed" or item.get("executed") is not True
                for item in checks.values()
            ):
                raise AuthoringError("Required checks have not passed; draft remains unpublished")
            previous = (
                db.get(FunctionRelease, function.current_release_id)
                if function.current_release_id
                else None
            )
            if previous and (previous.release_metadata or {}).get("revision_id") == current["id"]:
                return {
                    "function_id": function_id,
                    "release_id": previous.id,
                    "revision_hash": expected_revision,
                    "already_published": True,
                }
            latest = db.execute(
                select(FunctionRelease.version)
                .where(FunctionRelease.function_id == function_id)
                .order_by(FunctionRelease.version.desc())
                .limit(1)
            ).scalar()
            release = FunctionRelease(
                function_id=function_id,
                version=(latest or 0) + 1,
                code_snapshot=current["code"],
                dependency_manifest=current["dependencies"],
                release_metadata={
                    "revision_id": current["id"],
                    "revision_hash": expected_revision,
                    "validation_id": validation_id,
                    "checks": report["checks"],
                },
            )
            db.add(release)
            db.flush()
            function.current_release_id = release.id
            function.status = "released"
            return {
                "function_id": function_id,
                "release_id": release.id,
                "revision_hash": expected_revision,
                "already_published": False,
            }
