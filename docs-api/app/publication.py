"""Revision-bound change validation and direct publication.

Publishing is a contributor action. Review comments are optional audit events and
never authorize or block a release.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg2.extras
from fastapi import HTTPException

from app import generator
from app.agent_auth import Principal
from app.db import get_conn
from app.event_store import append_event
from app.markdown_sections import find_section
from app.runtime import deploy_current_state, state_identity

_PATH_RE = re.compile(r"^[a-z0-9/_-]+\.md$")
_OPERATION_TYPES = {
    "CREATE_PAGE",
    "REPLACE_DOCUMENT",
    "PATCH_METADATA",
    "REPLACE_SECTION",
    "INSERT_BEFORE_HEADING",
    "INSERT_AFTER_HEADING",
    "MOVE_PAGE",
    "REPARENT_NAV",
    "ARCHIVE_PAGE",
    "RESTORE_PAGE",
    "ADD_REDIRECT",
    "REMOVE_REDIRECT",
    "REORDER_SECTIONS",
}


def operation_types() -> list[str]:
    return sorted(_OPERATION_TYPES)


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _load_pages(conn, *, for_update: bool = False) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.resource_id::text, p.path, p.title, p.nav_path, p.content,
               p.revision, p.version, p.status, p.workspace_id::text,
               w.workspace_key, p.publication_state, p.knowledge_class,
               p.verification_state, p.owner_principal_id::text,
               p.review_due_at, p.criticality, p.metadata_review_required,
               p.metadata_version, p.updated_at, p.updated_by, p.provenance
          FROM docs.pages p
          JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
         ORDER BY p.path
        """
        + (" FOR UPDATE OF p" if for_update else ""),
    )
    keys = (
        "resource_id", "path", "title", "nav_path", "content", "revision",
        "version", "status", "workspace_id", "workspace_key", "publication_state",
        "knowledge_class", "verification_state", "owner_principal_id", "review_due_at",
        "criticality", "metadata_review_required", "metadata_version", "updated_at",
        "updated_by", "provenance",
    )
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _load_redirects(conn) -> dict[str, str]:
    cur = conn.cursor()
    cur.execute("SELECT from_path, to_path FROM docs.redirects ORDER BY from_path")
    return {row[0]: row[1] for row in cur.fetchall()}


def _load_sections(conn) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute("SELECT name, sort_order FROM docs.sections ORDER BY sort_order, name")
    return {row[0]: int(row[1]) for row in cur.fetchall()}


def _load_operations(conn, change_id: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT operation_id::text, sequence, idempotency_key, operation_type,
               page_resource_id::text, expected_revision, expected_section_hash,
               payload, validation_result, created_at
          FROM docs.change_operations
         WHERE change_id = %s
         ORDER BY sequence, operation_id
        """,
        (change_id,),
    )
    keys = (
        "operation_id", "sequence", "idempotency_key", "operation_type",
        "page_resource_id", "expected_revision", "expected_section_hash",
        "payload", "validation_result", "created_at",
    )
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _change(conn, change_id: str, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT change_id::text, workspace_key, title, purpose,
               author_principal_id::text, idempotency_key, status,
               base_state_identity, validation_summary, preview_receipt,
               publication_receipt, failure_receipt, created_at, updated_at,
               published_at
          FROM docs.changes
         WHERE change_id = %s
        """
        + (" FOR UPDATE" if for_update else ""),
        (change_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CHANGE_NOT_FOUND"})
    keys = (
        "change_id", "workspace_key", "title", "purpose", "author_principal_id",
        "idempotency_key", "status", "base_state_identity", "validation_summary",
        "preview_receipt", "publication_receipt", "failure_receipt", "created_at",
        "updated_at", "published_at",
    )
    return dict(zip(keys, row))


def change_view(conn, change_id: str) -> dict[str, Any]:
    change = _change(conn, change_id)
    change["operations"] = _load_operations(conn, change_id)
    return change


def _replace_section(content: str, heading_id: str, replacement: str) -> str:
    section = find_section(content, heading_id)
    if section is None:
        raise ValueError("SECTION_NOT_FOUND")
    lines = content.splitlines(keepends=True)
    normalized = replacement.rstrip() + "\n"
    return "".join(lines[: section.start_line - 1]) + normalized + "".join(lines[section.end_line :])


def _insert_at_heading(content: str, heading_id: str, inserted: str, *, after: bool) -> str:
    section = find_section(content, heading_id)
    if section is None:
        raise ValueError("SECTION_NOT_FOUND")
    lines = content.splitlines(keepends=True)
    index = section.end_line if after else section.start_line - 1
    normalized = inserted.rstrip() + "\n\n"
    return "".join(lines[:index]) + normalized + "".join(lines[index:])


def _require_payload(operation: dict[str, Any], *fields: str) -> None:
    missing = [field for field in fields if field not in operation["payload"]]
    if missing:
        raise ValueError("MISSING_PAYLOAD:" + ",".join(missing))


def _workspace_id(conn, workspace_key: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT workspace_id::text FROM docplane.workspaces WHERE workspace_key = %s", (workspace_key,))
    row = cur.fetchone()
    if row is None:
        raise ValueError("WORKSPACE_NOT_FOUND")
    return row[0]


def _record_operation_result(
    result: dict[str, Any],
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    """Roll one operation receipt into the change-level validation result.

    Every exit path must pass through this function. Otherwise a rejected
    operation can disappear from the change-level error set and be published as
    a successful no-op.
    """
    result["accepted"] = not result["errors"]
    results.append(result)
    errors.extend(result["errors"])
    warnings.extend(result["warnings"])


def generated_guard_error(provenance: str | None, declared_by: str | None, acting_principal_id: str | None) -> dict[str, Any] | None:
    """A GENERATED page is a certified derived artifact: only the principal
    that declared its generating artifact may publish to it while the
    declaration stands. Remediation is regeneration; retire the declaration
    to hand-edit. Pure so the rule is testable without a database."""
    if provenance != "GENERATED":
        return None
    if declared_by is None:
        # Declaration retired or absent: the page is hand-editable again.
        return None
    if acting_principal_id is not None and str(acting_principal_id) == str(declared_by):
        return None
    return {
        "code": "PROVENANCE_GENERATED_PAGE_PROTECTED",
        "message": "This page is a generated artifact; remediation is regeneration from the authoritative source.",
        "remedy": "Run the declaring generator, or retire the artifact declaration (POST /api/v1/model/artifacts/{artifact_id}/retire) before hand-editing.",
    }


def _declaring_artifact_principal(conn, page_resource_id: str) -> str | None:
    """Ownership binds to the stable page identity: at most one active
    declaration can target a page (UNIQUE on model.artifact_targets), so
    this is deterministic and survives page moves."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.declared_by::text
          FROM model.artifact_targets t
          JOIN model.generated_artifacts a ON a.artifact_id = t.artifact_id
         WHERE t.page_resource_id = %s AND a.status = 'DECLARED'
        """,
        (page_resource_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def evaluate_change(conn, change: dict[str, Any], operations: list[dict[str, Any]], *, lock: bool = False, acting_principal_id: str | None = None) -> dict[str, Any]:
    original_pages = _load_pages(conn, for_update=lock)
    pages = copy.deepcopy(original_pages)
    original_redirects = _load_redirects(conn)
    redirects = dict(original_redirects)
    original_sections = _load_sections(conn)
    sections = dict(original_sections)
    by_id = {page["resource_id"]: page for page in pages}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    touched: set[str] = set()
    created: set[str] = set()

    base_identity = state_identity(original_pages, original_sections, original_redirects)
    if change.get("base_state_identity") and change["base_state_identity"] != base_identity:
        errors.append(
            {
                "code": "BASE_STATE_IDENTITY_STALE",
                "expected": change["base_state_identity"],
                "current": base_identity,
            }
        )

    for operation in operations:
        result = {
            "operation_id": operation["operation_id"],
            "sequence": operation["sequence"],
            "operation_type": operation["operation_type"],
            "errors": [],
            "warnings": [],
        }
        op_errors = result["errors"]
        op_type = operation["operation_type"]
        if op_type not in _OPERATION_TYPES:
            op_errors.append({"code": "OPERATION_UNSUPPORTED", "operation_type": op_type})
            _record_operation_result(result, results, errors, warnings)
            continue
        page = by_id.get(operation.get("page_resource_id")) if operation.get("page_resource_id") else None
        if op_type not in {"CREATE_PAGE", "ADD_REDIRECT", "REMOVE_REDIRECT", "REORDER_SECTIONS"}:
            if page is None:
                op_errors.append({"code": "PAGE_NOT_FOUND", "resource_id": operation.get("page_resource_id")})
            elif page["revision"] != operation.get("expected_revision"):
                op_errors.append(
                    {
                        "code": "PAGE_REVISION_STALE",
                        "expected": operation.get("expected_revision"),
                        "current": page["revision"],
                        "current_version": page["version"],
                        "current_content_sha256": hashlib.sha256(page["content"].encode("utf-8")).hexdigest(),
                        "message": "The page changed after this operation was prepared.",
                        "remedy": "Fetch the current page, rebase the intended edit, and resubmit with the current revision.",
                    }
                )
            elif page.get("provenance") == "GENERATED":
                guard = generated_guard_error(page["provenance"], _declaring_artifact_principal(conn, page["resource_id"]), acting_principal_id)
                if guard is not None:
                    if acting_principal_id is None:
                        # Validation dry-run: surface the constraint; enforcement
                        # binds to the actual publishing principal at publish.
                        result["warnings"].append({**guard, "enforced_at": "publish"})
                    else:
                        op_errors.append(guard)
        if op_errors:
            _record_operation_result(result, results, errors, warnings)
            continue
        try:
            payload = operation["payload"]
            if op_type == "CREATE_PAGE":
                _require_payload(operation, "path", "title", "nav_path", "content")
                path = str(payload["path"])
                if not _PATH_RE.fullmatch(path):
                    raise ValueError("PATH_INVALID")
                if any(candidate["path"] == path and candidate["status"] != "archived" for candidate in pages):
                    raise ValueError("CREATE_PATH_EXISTS")
                workspace_key = str(payload.get("workspace_key") or change["workspace_key"])
                resource_id = str(payload.get("resource_id") or uuid4())
                created_page = {
                    "resource_id": resource_id,
                    "path": path,
                    "title": str(payload["title"]).strip(),
                    "nav_path": str(payload["nav_path"]).strip(),
                    "content": str(payload["content"]),
                    "revision": str(uuid4()),
                    "version": 1,
                    "status": "active",
                    "workspace_id": _workspace_id(conn, workspace_key),
                    "workspace_key": workspace_key,
                    "publication_state": "PUBLISHED",
                    "knowledge_class": payload.get("knowledge_class"),
                    "verification_state": "UNVERIFIED",
                    "owner_principal_id": None,
                    "review_due_at": None,
                    "criticality": str(payload.get("criticality", "NORMAL")),
                    "metadata_review_required": False,
                    "metadata_version": 1,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": "pending",
                }
                pages.append(created_page)
                by_id[resource_id] = created_page
                created.add(resource_id)
                touched.add(resource_id)
                result["resource_id"] = resource_id
            elif op_type == "REPLACE_DOCUMENT":
                _require_payload(operation, "content")
                page["content"] = str(payload["content"])
                for field in ("title", "nav_path"):
                    if field in payload:
                        page[field] = str(payload[field]).strip()
                touched.add(page["resource_id"])
            elif op_type == "PATCH_METADATA":
                allowed = {"title", "nav_path", "workspace_key", "knowledge_class", "criticality"}
                unexpected = set(payload) - allowed
                if unexpected:
                    raise ValueError("METADATA_FIELD_UNSUPPORTED:" + ",".join(sorted(unexpected)))
                for field in ("title", "nav_path", "knowledge_class", "criticality"):
                    if field in payload:
                        page[field] = payload[field]
                if "workspace_key" in payload:
                    page["workspace_key"] = str(payload["workspace_key"])
                    page["workspace_id"] = _workspace_id(conn, page["workspace_key"])
                page["metadata_version"] += 1
                page["metadata_review_required"] = False
                touched.add(page["resource_id"])
            elif op_type in {"REPLACE_SECTION", "INSERT_BEFORE_HEADING", "INSERT_AFTER_HEADING"}:
                _require_payload(operation, "heading_id", "content")
                heading_id = str(payload["heading_id"])
                current = find_section(page["content"], heading_id)
                if current is None:
                    raise ValueError("SECTION_NOT_FOUND")
                if not current.explicit_id:
                    raise ValueError("SECTION_ID_NOT_EXPLICIT")
                if operation.get("expected_section_hash") != current.content_hash:
                    raise ValueError("SECTION_HASH_STALE")
                if op_type == "REPLACE_SECTION":
                    page["content"] = _replace_section(page["content"], heading_id, str(payload["content"]))
                else:
                    page["content"] = _insert_at_heading(
                        page["content"],
                        heading_id,
                        str(payload["content"]),
                        after=op_type == "INSERT_AFTER_HEADING",
                    )
                touched.add(page["resource_id"])
            elif op_type == "MOVE_PAGE":
                _require_payload(operation, "to_path")
                old_path = page["path"]
                new_path = str(payload["to_path"])
                if not _PATH_RE.fullmatch(new_path):
                    raise ValueError("PATH_INVALID")
                if any(candidate["resource_id"] != page["resource_id"] and candidate["path"] == new_path and candidate["status"] != "archived" for candidate in pages):
                    raise ValueError("TARGET_PATH_COLLISION")
                page["path"] = new_path
                if payload.get("to_nav_path"):
                    page["nav_path"] = str(payload["to_nav_path"])
                if payload.get("create_redirect", True):
                    redirects[old_path] = new_path
                touched.add(page["resource_id"])
            elif op_type == "REPARENT_NAV":
                _require_payload(operation, "to_nav_path")
                page["nav_path"] = str(payload["to_nav_path"])
                touched.add(page["resource_id"])
            elif op_type == "ARCHIVE_PAGE":
                page["status"] = "archived"
                page["publication_state"] = "ARCHIVED"
                touched.add(page["resource_id"])
            elif op_type == "RESTORE_PAGE":
                page["status"] = "active"
                page["publication_state"] = "PUBLISHED"
                touched.add(page["resource_id"])
            elif op_type == "ADD_REDIRECT":
                _require_payload(operation, "from_path", "to_path")
                source, target = str(payload["from_path"]), str(payload["to_path"])
                if not _PATH_RE.fullmatch(source) or not _PATH_RE.fullmatch(target):
                    raise ValueError("PATH_INVALID")
                if any(candidate["path"] == source and candidate["status"] == "active" for candidate in pages):
                    raise ValueError("REDIRECT_SOURCE_IS_ACTIVE_PAGE")
                redirects[source] = target
            elif op_type == "REMOVE_REDIRECT":
                _require_payload(operation, "from_path")
                redirects.pop(str(payload["from_path"]), None)
            elif op_type == "REORDER_SECTIONS":
                _require_payload(operation, "order")
                order = payload["order"]
                if not isinstance(order, list) or len(order) != len(set(order)):
                    raise ValueError("SECTION_ORDER_INVALID")
                sections = {str(name): index for index, name in enumerate(order)}
        except ValueError as exc:
            code, _, detail = str(exc).partition(":")
            entry = {"code": code}
            if detail:
                entry["detail"] = detail
            op_errors.append(entry)
        _record_operation_result(result, results, errors, warnings)

    if not operations:
        errors.append({"code": "CHANGE_HAS_NO_OPERATIONS"})
    active_pages = [page for page in pages if page["status"] == "active"]
    active_paths = [page["path"] for page in active_pages]
    if len(active_paths) != len(set(active_paths)):
        errors.append({"code": "ACTIVE_PATH_COLLISION"})
    redirect_targets = {page["path"] for page in active_pages}
    for source, target in redirects.items():
        if source == target:
            errors.append({"code": "REDIRECT_SELF_LOOP", "path": source})
        if target not in redirect_targets:
            errors.append({"code": "REDIRECT_TARGET_MISSING", "from_path": source, "to_path": target})
    try:
        generator.build_nav(active_pages, section_order=sections)
    except generator.NavConflict as exc:
        errors.append({"code": "NAV_CONFLICT", "kind": exc.kind, "segment": exc.segment, "message": exc.message})

    candidate_identity = _canonical_hash(
        {
            "pages": [
                {
                    "resource_id": page["resource_id"],
                    "path": page["path"],
                    "nav_path": page["nav_path"],
                    "content_hash": hashlib.sha256(page["content"].encode("utf-8")).hexdigest(),
                    "status": page["status"],
                }
                for page in sorted(pages, key=lambda item: item["path"])
            ],
            "redirects": redirects,
            "sections": sections,
        }
    )
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "operations": results,
        "operations_checked": len(operations),
        "operations_accepted": sum(1 for result in results if result["accepted"]),
        "base_state_identity": base_identity,
        "candidate_identity": candidate_identity,
        "pages": pages,
        "original_pages": original_pages,
        "redirects": redirects,
        "original_redirects": original_redirects,
        "sections": sections,
        "original_sections": original_sections,
        "touched": sorted(touched),
        "created": sorted(created),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": "direct-publication-v1",
    }


def validate_change(change_id: UUID | str, principal: Principal) -> dict[str, Any]:
    with get_conn() as conn:
        change = _change(conn, str(change_id), for_update=True)
        if change["status"] in {"PUBLISHED", "ABANDONED"}:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_NOT_MUTABLE", "status": change["status"]})
        operations = _load_operations(conn, str(change_id))
        evaluation = evaluate_change(conn, change, operations)
        summary = {key: evaluation[key] for key in ("passed", "errors", "warnings", "operations_checked", "operations_accepted", "base_state_identity", "candidate_identity", "validated_at", "contract_version")}
        preview = {
            "candidate_identity": evaluation["candidate_identity"],
            "operations": evaluation["operations"],
            "publication": {"supported": evaluation["passed"], "review_required": False},
        }
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.changes
               SET status = %s, validation_summary = %s, preview_receipt = %s,
                   failure_receipt = NULL, updated_at = now()
             WHERE change_id = %s
            """,
            ("VALIDATED" if evaluation["passed"] else "DRAFT", _json(summary), _json(preview), str(change_id)),
        )
        for result in evaluation["operations"]:
            cur.execute(
                "UPDATE docs.change_operations SET validation_result = %s WHERE operation_id = %s",
                (_json(result), result["operation_id"]),
            )
        append_event(
            conn,
            event_type="CHANGE_VALIDATED",
            channel="API",
            producer_id="docplane-publication",
            idempotency_key=f"CHANGE_VALIDATED:{change_id}:{evaluation['candidate_identity']}",
            principal=principal,
            workspace_key=change["workspace_key"],
            resource_type="CHANGE",
            resource_id=str(change_id),
            metadata={"passed": evaluation["passed"], "errors": len(evaluation["errors"])},
        )
        conn.commit()
        return change_view(conn, str(change_id))


def _snapshot_page(cur, page: dict[str, Any], change_id: str) -> None:
    cur.execute(
        """
        INSERT INTO docs.page_versions
            (resource_id, path, title, nav_path, content, revision, version,
             status, workspace_id, publication_state, knowledge_class,
             verification_state, owner_principal_id, review_due_at, criticality,
             metadata_review_required, metadata_version, updated_by, change_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        -- A (resource_id, revision) pair identifies an immutable version, so if
        -- it is already recorded this snapshot is a no-op. Migration-imported
        -- pages carry their head revision in page_versions (full faithful
        -- history), which would otherwise collide the first time the page is
        -- edited/restored through the publish path. Skip the duplicate; history
        -- is preserved exactly.
        ON CONFLICT (resource_id, revision) DO NOTHING
        """,
        (
            page["resource_id"], page["path"], page["title"], page["nav_path"],
            page["content"], page["revision"], page["version"], page["status"],
            page["workspace_id"], page["publication_state"], page["knowledge_class"],
            page["verification_state"], page["owner_principal_id"], page["review_due_at"],
            page["criticality"], page["metadata_review_required"], page["metadata_version"],
            page["updated_by"], change_id,
        ),
    )


def publish_change(change_id: UUID | str, principal: Principal) -> dict[str, Any]:
    change_id = str(change_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('docplane-publication'))")
        change = _change(conn, change_id, for_update=True)
        if change["status"] == "PUBLISHED":
            return change_view(conn, change_id)
        if change["status"] == "ABANDONED":
            raise HTTPException(status_code=409, detail={"code": "CHANGE_ABANDONED"})
        operations = _load_operations(conn, change_id)
        evaluation = evaluate_change(conn, change, operations, lock=True, acting_principal_id=str(principal.principal_id))
        if not evaluation["passed"]:
            summary = {key: evaluation[key] for key in ("passed", "errors", "warnings", "operations_checked", "operations_accepted", "base_state_identity", "candidate_identity", "validated_at", "contract_version")}
            cur.execute(
                "UPDATE docs.changes SET status = 'DRAFT', validation_summary = %s, updated_at = now() WHERE change_id = %s",
                (_json(summary), change_id),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={"code": "CHANGE_VALIDATION_FAILED", "validation": summary})

        original = {page["resource_id"]: page for page in evaluation["original_pages"]}
        candidate = {page["resource_id"]: page for page in evaluation["pages"]}
        for resource_id in evaluation["touched"]:
            page = candidate[resource_id]
            if resource_id in evaluation["created"]:
                cur.execute(
                    """
                    INSERT INTO docs.pages
                        (resource_id, path, title, nav_path, content, revision, version,
                         status, workspace_id, publication_state, knowledge_class,
                         verification_state, owner_principal_id, review_due_at,
                         criticality, metadata_review_required, metadata_version,
                         updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        resource_id, page["path"], page["title"], page["nav_path"],
                        page["content"], page["revision"], page["status"], page["workspace_id"],
                        page["publication_state"], page["knowledge_class"], page["verification_state"],
                        page["owner_principal_id"], page["review_due_at"], page["criticality"],
                        page["metadata_review_required"], page["metadata_version"], principal.display_name,
                    ),
                )
                continue
            previous = original[resource_id]
            _snapshot_page(cur, previous, change_id)
            if previous["verification_state"] == "VERIFIED":
                page["verification_state"] = "OUTDATED"
                page["review_due_at"] = datetime.now(timezone.utc)
                cur.execute(
                    """
                    UPDATE docs.page_verifications
                       SET verification_state = 'OUTDATED'
                     WHERE page_resource_id = %s AND verification_state = 'VERIFIED'
                    """,
                    (resource_id,),
                )
            new_revision = str(uuid4())
            cur.execute(
                """
                UPDATE docs.pages
                   SET path = %s, title = %s, nav_path = %s, content = %s,
                       revision = %s, version = version + 1, status = %s,
                       workspace_id = %s, publication_state = %s,
                       knowledge_class = %s, verification_state = %s,
                       owner_principal_id = %s, review_due_at = %s,
                       criticality = %s, metadata_review_required = %s,
                       metadata_version = %s, updated_by = %s, updated_at = now()
                 WHERE resource_id = %s AND revision = %s
                """,
                (
                    page["path"], page["title"], page["nav_path"], page["content"],
                    new_revision, page["status"], page["workspace_id"], page["publication_state"],
                    page["knowledge_class"], page["verification_state"], page["owner_principal_id"],
                    page["review_due_at"], page["criticality"], page["metadata_review_required"],
                    page["metadata_version"], principal.display_name, resource_id, previous["revision"],
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail={"code": "PAGE_REVISION_RACE", "resource_id": resource_id})

        for source in set(evaluation["original_redirects"]) - set(evaluation["redirects"]):
            cur.execute("DELETE FROM docs.redirects WHERE from_path = %s", (source,))
        for source, target in evaluation["redirects"].items():
            if evaluation["original_redirects"].get(source) == target:
                continue
            cur.execute(
                """
                INSERT INTO docs.redirects (from_path, to_path, revision, version, updated_by)
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (from_path) DO UPDATE
                    SET to_path = EXCLUDED.to_path, revision = EXCLUDED.revision,
                        version = docs.redirects.version + 1,
                        updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                (source, target, str(uuid4()), principal.display_name),
            )
        if evaluation["sections"] != evaluation["original_sections"]:
            cur.execute("DELETE FROM docs.sections")
            for name, order in evaluation["sections"].items():
                cur.execute(
                    "INSERT INTO docs.sections (name, sort_order, updated_by) VALUES (%s, %s, %s)",
                    (name, order, principal.display_name),
                )

        operations_applied = sum(1 for result in evaluation["operations"] if result["accepted"])
        receipt = {
            "contract_version": "direct-publication-v1",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "published_by": principal.principal_id,
            "candidate_identity": evaluation["candidate_identity"],
            "operations_submitted": len(operations),
            "operations_applied": operations_applied,
            "review_required": False,
            "database_transaction": "COMMITTED",
        }
        cur.execute(
            """
            UPDATE docs.changes
               SET status = 'PUBLISHED', validation_summary = %s,
                   preview_receipt = %s, publication_receipt = %s,
                   failure_receipt = NULL, published_at = now(), updated_at = now()
             WHERE change_id = %s
            """,
            (
                _json({key: evaluation[key] for key in ("passed", "errors", "warnings", "operations_checked", "operations_accepted", "base_state_identity", "candidate_identity", "validated_at", "contract_version")}),
                _json({"candidate_identity": evaluation["candidate_identity"], "operations": evaluation["operations"]}),
                _json(receipt),
                change_id,
            ),
        )
        append_event(
            conn,
            event_type="CHANGE_PUBLISHED",
            channel="API",
            producer_id="docplane-publication",
            idempotency_key=f"CHANGE_PUBLISHED:{change_id}:{evaluation['candidate_identity']}",
            principal=principal,
            workspace_key=change["workspace_key"],
            resource_type="CHANGE",
            resource_id=change_id,
            metadata={"operations": operations_applied, "candidate_identity": evaluation["candidate_identity"]},
        )
        conn.commit()

    deployment = deploy_current_state(requested_by=principal.principal_id, change_id=change_id)
    with get_conn() as conn:
        current = _change(conn, change_id, for_update=True)
        updated_receipt = dict(current.get("publication_receipt") or receipt)
        updated_receipt["deployment"] = deployment
        cur = conn.cursor()
        cur.execute(
            "UPDATE docs.changes SET publication_receipt = %s, updated_at = now() WHERE change_id = %s",
            (_json(updated_receipt), change_id),
        )
        conn.commit()
        return change_view(conn, change_id)
