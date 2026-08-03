"""Model-domain API: the card index of what the fabric structurally is."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from app.agent_auth import Principal, require_bootstrap_token, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt
from app.model_contracts import CARD_CONTRACTS, checklist_errors, contracts_document, secret_findings
from app.model_models import (
    ArtifactDeclare,
    ArtifactCustodyReassign,
    ArtifactRetire,
    EntityCreate,
    EntityLinkCreate,
    EntityLinkRemove,
    EntityPageLinkCreate,
    EntityRetire,
    EntityUpdate,
)

router = APIRouter(tags=["model-v1"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _guard_attributes(kind: str, attributes: dict[str, Any]) -> None:
    findings = secret_findings(attributes)
    if findings:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ATTRIBUTES_SECRET_SHAPED", "findings": findings})
    errors = checklist_errors(kind, attributes)
    if errors:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ATTRIBUTES_CHECKLIST_FAILED", "kind": kind, "errors": errors, "contract": "/api/v1/model/contracts"})


_ENTITY_COLUMNS = (
    "entity_id", "entity_kind", "entity_key", "display_name", "attributes",
    "owner_principal_id", "created_by", "status", "retired_at", "version",
    "created_at", "updated_at",
)


def _entity_select() -> str:
    return (
        "SELECT entity_id, entity_kind, entity_key, display_name, attributes, "
        "owner_principal_id, created_by, status, retired_at, version, "
        "created_at, updated_at FROM model.entities"
    )


def _entity(row) -> dict[str, Any]:
    value = dict(zip(_ENTITY_COLUMNS, row))
    for key in ("entity_id", "owner_principal_id", "created_by"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://model/{value['entity_kind'].lower()}/{value['entity_key']}"
    return value


def _load_entity(conn, entity_id: UUID, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(_entity_select() + " WHERE entity_id = %s" + (" FOR UPDATE" if for_update else ""), (str(entity_id),))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_ENTITY_NOT_FOUND"})
    return _entity(row)


@router.get("/api/v1/model/contracts")
def model_contracts(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    return contracts_document()


@router.post("/api/v1/model/entities", status_code=201)
def create_entity(
    request: EntityCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "entity-create", **request.model_dump(mode="json")})
    _guard_attributes(request.entity_kind, request.attributes)
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ENTITY_CREATE", digest)
        if replayed is not None:
            return replayed
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO model.entities
                    (entity_kind, entity_key, display_name, attributes, owner_principal_id, created_by, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (created_by, idempotency_key)
                DO UPDATE SET updated_at = model.entities.updated_at
                RETURNING entity_id
                """,
                (
                    request.entity_kind, request.entity_key, request.display_name.strip(),
                    _json(request.attributes),
                    str(request.owner_principal_id) if request.owner_principal_id else None,
                    principal.principal_id, key,
                ),
            )
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail={"code": "MODEL_ENTITY_KEY_EXISTS"}) from exc
        entity_id = cur.fetchone()[0]
        response = _load_entity(conn, entity_id)
        append_event(
            conn,
            event_type="MODEL_ENTITY_CREATED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_CREATED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"entity_kind": request.entity_kind, "entity_key": request.entity_key},
        )
        save_receipt(conn, principal, key, "MODEL_ENTITY_CREATE", str(entity_id), digest, response)
        conn.commit()
        return response


@router.get("/api/v1/model/entities")
def list_entities(
    entity_kind: str | None = None,
    status: str = Query(default="ACTIVE"),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if status not in {"ACTIVE", "RETIRED", "all"}:
        raise HTTPException(status_code=422, detail={"code": "MODEL_STATUS_INVALID"})
    predicates: list[str] = []
    params: list[Any] = []
    if entity_kind is not None:
        if entity_kind not in CARD_CONTRACTS:
            raise HTTPException(status_code=422, detail={"code": "MODEL_KIND_INVALID", "allowed": sorted(CARD_CONTRACTS)})
        predicates.append("entity_kind = %s")
        params.append(entity_kind)
    if status != "all":
        predicates.append("status = %s")
        params.append(status)
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM model.entities{where}", params)
        total = int(cur.fetchone()[0])
        cur.execute(_entity_select() + where + " ORDER BY entity_kind, entity_key LIMIT %s", [*params, limit])
        rows = cur.fetchall()
    values = [_entity(row) for row in rows]
    return {"entities": values, "count": len(values), "total": total, "truncated": len(values) < total}


@router.get("/api/v1/model/entities/{entity_id}")
def get_entity(entity_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        value = _load_entity(conn, entity_id)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT l.relation, l.to_entity_id::text, e.entity_kind, e.entity_key, l.note, l.metadata, l.created_at
              FROM model.entity_links l JOIN model.entities e ON e.entity_id = l.to_entity_id
             WHERE l.from_entity_id = %s ORDER BY l.relation, e.entity_kind, e.entity_key
            """,
            (str(entity_id),),
        )
        links_out = [dict(zip(("relation", "to_entity_id", "entity_kind", "entity_key", "note", "metadata", "created_at"), row)) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT l.relation, l.from_entity_id::text, e.entity_kind, e.entity_key, l.note, l.metadata, l.created_at
              FROM model.entity_links l JOIN model.entities e ON e.entity_id = l.from_entity_id
             WHERE l.to_entity_id = %s ORDER BY l.relation, e.entity_kind, e.entity_key
            """,
            (str(entity_id),),
        )
        links_in = [dict(zip(("relation", "from_entity_id", "entity_kind", "entity_key", "note", "metadata", "created_at"), row)) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT l.relation, l.page_resource_id::text, p.path, p.title, l.created_at
              FROM model.entity_page_links l JOIN docs.pages p ON p.resource_id = l.page_resource_id
             WHERE l.entity_id = %s ORDER BY l.relation, p.path
            """,
            (str(entity_id),),
        )
        pages = [dict(zip(("relation", "page_resource_id", "path", "title", "created_at"), row)) for row in cur.fetchall()]
    return {**value, "links_out": links_out, "links_in": links_in, "pages": pages}


@router.post("/api/v1/model/entities/{entity_id}/update")
def update_entity(
    entity_id: UUID,
    request: EntityUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "entity-update", "entity_id": str(entity_id), **request.model_dump(mode="json")})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ENTITY_UPDATE", digest)
        if replayed is not None:
            return replayed
        entity = _load_entity(conn, entity_id, for_update=True)
        if entity["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ENTITY_VERSION_STALE", "current": entity["version"]})
        attributes = request.attributes if request.attributes is not None else entity["attributes"]
        _guard_attributes(entity["entity_kind"], attributes)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE model.entities
               SET display_name = %s, attributes = %s, owner_principal_id = %s,
                   version = version + 1, updated_at = now()
             WHERE entity_id = %s AND version = %s
            """,
            (
                (request.display_name or entity["display_name"]).strip(),
                _json(attributes),
                str(request.owner_principal_id) if request.owner_principal_id else entity["owner_principal_id"],
                str(entity_id), request.expected_version,
            ),
        )
        if request.attributes is not None and request.attributes != entity["attributes"]:
            # Graph ripple: reality moved, so prose describing this entity
            # becomes a verification candidate (deduplicated per entity).
            from app.verification_api import mint_ripple_request

            mint_ripple_request(conn, {**entity, "version": entity["version"] + 1}, principal)
        append_event(
            conn,
            event_type="MODEL_ENTITY_UPDATED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_UPDATED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"entity_kind": entity["entity_kind"], "entity_key": entity["entity_key"]},
        )
        response = _load_entity(conn, entity_id)
        save_receipt(conn, principal, key, "MODEL_ENTITY_UPDATE", str(entity_id), digest, response)
        conn.commit()
        return response


@router.post("/api/v1/model/entities/{entity_id}/retire")
def retire_entity(
    entity_id: UUID,
    request: EntityRetire,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        entity = _load_entity(conn, entity_id, for_update=True)
        if entity["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ENTITY_VERSION_STALE", "current": entity["version"]})
        if entity["status"] == "RETIRED":
            return entity
        cur = conn.cursor()
        cur.execute(
            "UPDATE model.entities SET status = 'RETIRED', retired_at = %s, version = version + 1, updated_at = now() WHERE entity_id = %s AND version = %s",
            (datetime.now(timezone.utc), str(entity_id), request.expected_version),
        )
        append_event(
            conn,
            event_type="MODEL_ENTITY_RETIRED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_RETIRED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"entity_kind": entity["entity_kind"], "entity_key": entity["entity_key"], "note": request.note},
        )
        conn.commit()
        return _load_entity(conn, entity_id)


@router.post("/api/v1/model/entities/{entity_id}/reactivate")
def reactivate_entity(
    entity_id: UUID,
    request: EntityRetire,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Retirement's counterpart: (entity_kind, entity_key) is unique across
    ALL statuses, so an identity that returns to reality (a rule restored in
    git) must resume its existing card rather than mint a duplicate key."""
    key = _key(idempotency_key)
    with get_conn() as conn:
        entity = _load_entity(conn, entity_id, for_update=True)
        if entity["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ENTITY_VERSION_STALE", "current": entity["version"]})
        if entity["status"] == "ACTIVE":
            return entity
        cur = conn.cursor()
        cur.execute(
            "UPDATE model.entities SET status = 'ACTIVE', retired_at = NULL, version = version + 1, updated_at = now() WHERE entity_id = %s AND version = %s",
            (str(entity_id), request.expected_version),
        )
        append_event(
            conn,
            event_type="MODEL_ENTITY_REACTIVATED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_REACTIVATED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"entity_kind": entity["entity_kind"], "entity_key": entity["entity_key"], "note": request.note},
        )
        conn.commit()
        return _load_entity(conn, entity_id)


@router.post("/api/v1/model/entities/{entity_id}/links", status_code=201)
def create_entity_link(
    entity_id: UUID,
    request: EntityLinkCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "entity-link", "entity_id": str(entity_id), **request.model_dump(mode="json")})
    findings = secret_findings(request.metadata)
    if findings:
        raise HTTPException(status_code=422, detail={"code": "MODEL_LINK_METADATA_SECRET_SHAPED", "findings": findings})
    if request.to_entity_id == entity_id:
        raise HTTPException(status_code=422, detail={"code": "MODEL_LINK_SELF"})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ENTITY_LINK", digest)
        if replayed is not None:
            return replayed
        _load_entity(conn, entity_id)
        _load_entity(conn, request.to_entity_id)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO model.entity_links (from_entity_id, relation, to_entity_id, note, metadata, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (from_entity_id, relation, to_entity_id)
            DO UPDATE SET metadata = EXCLUDED.metadata
             WHERE EXCLUDED.metadata <> '{}'::jsonb
            """,
            (str(entity_id), request.relation, str(request.to_entity_id), request.note, _json(request.metadata), principal.principal_id),
        )
        append_event(
            conn,
            event_type="MODEL_ENTITY_LINKED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_LINKED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"relation": request.relation, "to_entity_id": str(request.to_entity_id), "link_metadata": request.metadata},
        )
        response = {"from_entity_id": str(entity_id), **request.model_dump(mode="json")}
        save_receipt(conn, principal, key, "MODEL_ENTITY_LINK", str(entity_id), digest, response)
        conn.commit()
    return response


@router.post("/api/v1/model/entities/{entity_id}/links/remove")
def remove_entity_link(
    entity_id: UUID,
    request: EntityLinkRemove,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Reconciliation counterpart of link creation: a graph edge that no
    longer reflects reality (a rule's service label moved, a wire was
    re-plugged) is removed, not left to accumulate. Removing an absent link
    succeeds with removed=false so importers can converge idempotently."""
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "entity-unlink", "entity_id": str(entity_id), **request.model_dump(mode="json")})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ENTITY_UNLINK", digest)
        if replayed is not None:
            return replayed
        _load_entity(conn, entity_id)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM model.entity_links WHERE from_entity_id = %s AND relation = %s AND to_entity_id = %s",
            (str(entity_id), request.relation, str(request.to_entity_id)),
        )
        removed = cur.rowcount > 0
        append_event(
            conn,
            event_type="MODEL_ENTITY_UNLINKED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ENTITY_UNLINKED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ENTITY",
            resource_id=str(entity_id),
            metadata={"relation": request.relation, "to_entity_id": str(request.to_entity_id), "removed": removed, "note": request.note},
        )
        response = {"from_entity_id": str(entity_id), "relation": request.relation, "to_entity_id": str(request.to_entity_id), "removed": removed}
        save_receipt(conn, principal, key, "MODEL_ENTITY_UNLINK", str(entity_id), digest, response)
        conn.commit()
    return response


@router.post("/api/v1/model/entities/{entity_id}/page-links", status_code=201)
def create_entity_page_link(
    entity_id: UUID,
    request: EntityPageLinkCreate,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        _load_entity(conn, entity_id)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM docs.pages WHERE resource_id = %s", (str(request.page_resource_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
        cur.execute(
            """
            INSERT INTO model.entity_page_links (entity_id, relation, page_resource_id, created_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (str(entity_id), request.relation, str(request.page_resource_id), principal.principal_id),
        )
        conn.commit()
    return {"entity_id": str(entity_id), **request.model_dump(mode="json")}


_ARTIFACT_COLUMNS = (
    "artifact_id", "artifact_key", "generator_name", "generator_version",
    "config_hash", "source_entity_id", "redaction_policy", "target_page_paths",
    "declared_by", "status", "retired_at", "version", "created_at", "updated_at",
)


def _artifact_select() -> str:
    return (
        "SELECT artifact_id, artifact_key, generator_name, generator_version, "
        "config_hash, source_entity_id, redaction_policy, target_page_paths, "
        "declared_by, status, retired_at, version, created_at, updated_at "
        "FROM model.generated_artifacts"
    )


def _artifact(row) -> dict[str, Any]:
    value = dict(zip(_ARTIFACT_COLUMNS, row))
    for key in ("artifact_id", "source_entity_id", "declared_by"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://model/artifact/{value['artifact_key']}"
    return value


@router.get("/api/v1/model/artifacts")
def list_artifacts(
    status: str = Query(default="DECLARED"),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if status not in {"DECLARED", "RETIRED", "all"}:
        raise HTTPException(status_code=422, detail={"code": "MODEL_STATUS_INVALID"})
    predicate = "" if status == "all" else " WHERE status = %s"
    params: list[Any] = [] if status == "all" else [status]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM model.generated_artifacts{predicate}", params)
        total = int(cur.fetchone()[0])
        cur.execute(_artifact_select() + predicate + " ORDER BY artifact_key LIMIT %s", [*params, limit])
        rows = cur.fetchall()
    values = [_artifact(row) for row in rows]
    return {"artifacts": values, "count": len(values), "total": total, "truncated": len(values) < total}


@router.post("/api/v1/model/artifacts", status_code=201)
def declare_artifact(
    request: ArtifactDeclare,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "artifact-declare", **request.model_dump(mode="json")})
    if principal.principal_kind != "AUTOMATION":
        # Generated artifacts are published by a stable automation principal;
        # remediation is regeneration from the authoritative source.
        raise HTTPException(status_code=422, detail={"code": "MODEL_ARTIFACT_REQUIRES_AUTOMATION", "principal_kind": principal.principal_kind})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ARTIFACT_DECLARE", digest)
        if replayed is not None:
            return replayed
        _load_entity(conn, request.source_entity_id)
        cur = conn.cursor()
        if request.target_page_resource_ids:
            cur.execute("SELECT resource_id::text FROM docs.pages WHERE resource_id = ANY(%s::uuid[])", ([str(item) for item in request.target_page_resource_ids],))
            found = {row[0] for row in cur.fetchall()}
            missing = sorted({str(item) for item in request.target_page_resource_ids} - found)
            if missing:
                raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_TARGET_PAGE_NOT_FOUND", "missing": missing})
        try:
            cur.execute(
                """
                INSERT INTO model.generated_artifacts
                    (artifact_key, generator_name, generator_version, config_hash,
                     source_entity_id, redaction_policy, target_page_paths, declared_by, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (declared_by, idempotency_key)
                DO UPDATE SET updated_at = model.generated_artifacts.updated_at
                RETURNING artifact_id
                """,
                (
                    request.artifact_key, request.generator_name.strip(), request.generator_version.strip(),
                    request.config_hash, str(request.source_entity_id), request.redaction_policy.strip(),
                    _json(request.target_page_paths), principal.principal_id, key,
                ),
            )
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_KEY_EXISTS"}) from exc
        artifact_id = cur.fetchone()[0]
        for page_id in request.target_page_resource_ids:
            try:
                cur.execute(
                    "INSERT INTO model.artifact_targets (artifact_id, page_resource_id) VALUES (%s, %s) ON CONFLICT (artifact_id, page_resource_id) DO NOTHING",
                    (str(artifact_id), str(page_id)),
                )
            except psycopg2.errors.UniqueViolation as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_TARGET_CONFLICT", "page_resource_id": str(page_id), "message": "Another active declaration already owns this page."}) from exc
        # Declaration is the single act that both binds ownership and ARMS the
        # publication guard: the guard fires only on provenance = GENERATED,
        # and nothing else may set it — a page without a standing declaration
        # is hand-editable by definition.
        if request.target_page_resource_ids:
            cur.execute(
                "UPDATE docs.pages SET provenance = 'GENERATED', updated_at = now() WHERE resource_id = ANY(%s::uuid[])",
                ([str(item) for item in request.target_page_resource_ids],),
            )
        cur.execute(_artifact_select() + " WHERE artifact_id = %s", (str(artifact_id),))
        response = _artifact(cur.fetchone())
        append_event(
            conn,
            event_type="MODEL_ARTIFACT_DECLARED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ARTIFACT_DECLARED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ARTIFACT",
            resource_id=str(artifact_id),
            metadata={"artifact_key": request.artifact_key, "generator_name": request.generator_name},
        )
        save_receipt(conn, principal, key, "MODEL_ARTIFACT_DECLARE", str(artifact_id), digest, response)
        conn.commit()
        return response


@router.post("/api/v1/model/artifacts/{artifact_id}/retire")
def retire_artifact(
    artifact_id: UUID,
    request: ArtifactRetire,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_artifact_select() + " WHERE artifact_id = %s FOR UPDATE", (str(artifact_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
        artifact = _artifact(row)
        if artifact["declared_by"] != str(principal.principal_id):
            # Retirement dismantles generated-page protection, so it belongs
            # to the declaring automation principal alone. Anything else is
            # break-glass: an operator revoking/reissuing the declaring
            # principal through the audited bootstrap authority — never an
            # ordinary contributor silently releasing the pages.
            raise HTTPException(status_code=403, detail={
                "code": "MODEL_ARTIFACT_RETIRE_FORBIDDEN",
                "declared_by": artifact["declared_by"],
                "remedy": "Retire from the declaring automation principal, or use the operator break-glass path (bootstrap authority) if that principal is lost.",
            })
        if artifact["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_VERSION_STALE", "current": artifact["version"]})
        if artifact["status"] == "RETIRED":
            return artifact
        cur.execute(
            "UPDATE model.generated_artifacts SET status = 'RETIRED', retired_at = %s, version = version + 1, updated_at = now() WHERE artifact_id = %s AND version = %s",
            (datetime.now(timezone.utc), str(artifact_id), request.expected_version),
        )
        # Targets exist only while the declaration stands; removing them
        # releases the pages for hand-editing or a successor declaration —
        # and the provenance flag the declaration armed is released with them,
        # so the metadata surface never claims protection the guard no longer
        # enforces.
        cur.execute(
            """
            UPDATE docs.pages SET provenance = 'AUTHORED', updated_at = now()
             WHERE resource_id IN (SELECT page_resource_id FROM model.artifact_targets WHERE artifact_id = %s)
            """,
            (str(artifact_id),),
        )
        cur.execute("DELETE FROM model.artifact_targets WHERE artifact_id = %s", (str(artifact_id),))
        append_event(
            conn,
            event_type="MODEL_ARTIFACT_RETIRED",
            channel="API",
            producer_id="docplane-model",
            idempotency_key=f"MODEL_ARTIFACT_RETIRED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="MODEL_ARTIFACT",
            resource_id=str(artifact_id),
            metadata={"artifact_key": artifact["artifact_key"], "note": request.note},
        )
        conn.commit()
        cur.execute(_artifact_select() + " WHERE artifact_id = %s", (str(artifact_id),))
        return _artifact(cur.fetchone())


@router.post("/api/v1/bootstrap/model/artifacts/{artifact_id}/reassign-custody")
def reassign_artifact_custody(
    artifact_id: UUID,
    request: ArtifactCustodyReassign,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    bootstrap_token: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token"),
) -> dict[str, Any]:
    """Break-glass transfer of a live generated-artifact declaration.

    This does not weaken or release generated-page protection. It only moves
    retirement/regeneration custody to another active AUTOMATION principal.
    The bootstrap surface is used because the original principal may be lost.
    """
    require_bootstrap_token(bootstrap_token)
    key = _key(idempotency_key)
    payload = {"route": "artifact-reassign-custody", "artifact_id": str(artifact_id), **request.model_dump(mode="json")}
    digest = receipt_digest(payload)
    event_key = f"MODEL_ARTIFACT_CUSTODY_REASSIGNED:{key}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 74))", (f"bootstrap:{key}",))
        cur.execute(
            "SELECT metadata FROM docplane.events WHERE producer_id = 'docplane-bootstrap' AND idempotency_key = %s",
            (event_key,),
        )
        prior = cur.fetchone()
        if prior is not None:
            if (prior[0] or {}).get("request_hash") != digest:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "original_operation": "MODEL_ARTIFACT_CUSTODY_REASSIGN"})
            return prior[0]["response"]

        cur.execute(_artifact_select() + " WHERE artifact_id = %s FOR UPDATE", (str(artifact_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
        artifact = _artifact(row)
        if artifact["status"] != "DECLARED":
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_NOT_DECLARED", "status": artifact["status"]})
        if artifact["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_VERSION_STALE", "current": artifact["version"]})
        cur.execute(
            "SELECT principal_kind, status FROM docplane.principals WHERE principal_id = %s",
            (str(request.destination_principal_id),),
        )
        destination = cur.fetchone()
        if destination is None:
            raise HTTPException(status_code=404, detail={"code": "DESTINATION_PRINCIPAL_NOT_FOUND"})
        if destination != ("AUTOMATION", "ACTIVE"):
            raise HTTPException(status_code=422, detail={"code": "DESTINATION_PRINCIPAL_NOT_ACTIVE_AUTOMATION", "principal_kind": destination[0], "status": destination[1]})
        previous_principal_id = artifact["declared_by"]
        cur.execute(
            "UPDATE model.generated_artifacts SET declared_by = %s, version = version + 1, updated_at = now() WHERE artifact_id = %s AND version = %s",
            (str(request.destination_principal_id), str(artifact_id), request.expected_version),
        )
        cur.execute(_artifact_select() + " WHERE artifact_id = %s", (str(artifact_id),))
        response = jsonable_encoder(_artifact(cur.fetchone()))
        append_event(
            conn,
            event_type="MODEL_ARTIFACT_CUSTODY_REASSIGNED",
            channel="BOOTSTRAP",
            producer_id="docplane-bootstrap",
            idempotency_key=event_key,
            resource_type="MODEL_ARTIFACT",
            resource_id=str(artifact_id),
            metadata={
                "request_hash": digest,
                "artifact_key": artifact["artifact_key"],
                "previous_principal_id": previous_principal_id,
                "destination_principal_id": str(request.destination_principal_id),
                "purpose": request.purpose,
                "expected_version": request.expected_version,
                "response": response,
            },
        )
        conn.commit()
        return response
