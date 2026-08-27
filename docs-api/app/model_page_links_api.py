"""Exact-set MODEL -> KNOW CATALOGUES reconciliation.

CATALOGUES is a semantic relationship, not generated-artifact ownership. This
surface reconciles only CATALOGUES links for one entity and leaves every other
page-link relation untouched.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt

router = APIRouter(tags=["model-v1"])


class CataloguesPageLinkSet(BaseModel):
    page_resource_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def exact_set(self):
        if len(set(self.page_resource_ids)) != len(self.page_resource_ids):
            raise ValueError("page_resource_ids must be unique")
        return self


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _diff(current: set[str], desired: set[str]) -> tuple[list[str], list[str], list[str]]:
    return (
        sorted(desired - current),
        sorted(current - desired),
        sorted(current & desired),
    )


@router.put("/api/v1/model/entities/{entity_id}/page-links/catalogues")
def reconcile_catalogues_page_links(
    entity_id: UUID,
    request: CataloguesPageLinkSet,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Reconcile the complete active CATALOGUES page set for one entity.

    This deliberately does not touch DESCRIBES/OPERATES/DECIDES and does not
    confer generated-artifact ownership. Archived pages are rejected: active
    semantic catalogue links describe the current projection only.
    """
    key = _key(idempotency_key)
    desired = {str(value) for value in request.page_resource_ids}
    digest = receipt_digest(
        {
            "route": "entity-catalogues-page-links-exact-set",
            "entity_id": str(entity_id),
            "page_resource_ids": sorted(desired),
        }
    )

    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ENTITY_CATALOGUES_EXACT_SET", digest)
        if replayed is not None:
            return replayed

        cur = conn.cursor()
        cur.execute("SELECT status FROM model.entities WHERE entity_id = %s FOR UPDATE", (str(entity_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ENTITY_NOT_FOUND"})
        if row[0] != "ACTIVE":
            raise HTTPException(status_code=409, detail={"code": "MODEL_ENTITY_NOT_ACTIVE"})

        if desired:
            cur.execute(
                "SELECT resource_id::text, status FROM docs.pages WHERE resource_id = ANY(%s::uuid[])",
                (list(sorted(desired)),),
            )
            found = {resource_id: status for resource_id, status in cur.fetchall()}
            missing = sorted(desired - set(found))
            if missing:
                raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND", "page_resource_ids": missing})
            inactive = sorted(resource_id for resource_id, status in found.items() if status != "active")
            if inactive:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "CATALOGUES_PAGE_NOT_ACTIVE", "page_resource_ids": inactive},
                )

        cur.execute(
            "SELECT page_resource_id::text FROM model.entity_page_links WHERE entity_id = %s AND relation = 'CATALOGUES'",
            (str(entity_id),),
        )
        current = {row[0] for row in cur.fetchall()}
        added, removed, continuing = _diff(current, desired)

        if removed:
            cur.execute(
                "DELETE FROM model.entity_page_links WHERE entity_id = %s AND relation = 'CATALOGUES' AND page_resource_id = ANY(%s::uuid[])",
                (str(entity_id), removed),
            )
        if added:
            cur.executemany(
                """
                INSERT INTO model.entity_page_links (entity_id, relation, page_resource_id, created_by)
                VALUES (%s, 'CATALOGUES', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [(str(entity_id), resource_id, principal.principal_id) for resource_id in added],
            )

        changed = bool(added or removed)
        response = {
            "entity_id": str(entity_id),
            "relation": "CATALOGUES",
            "page_resource_ids": sorted(desired),
            "added": added,
            "removed": removed,
            "continuing": continuing,
            "changed": changed,
        }

        if changed:
            append_event(
                conn,
                event_type="MODEL_ENTITY_PAGE_CATALOGUES_RECONCILED",
                channel="API",
                producer_id="docplane-model",
                idempotency_key=f"MODEL_ENTITY_PAGE_CATALOGUES_RECONCILED:{principal.principal_id}:{key}",
                principal=principal,
                resource_type="MODEL_ENTITY",
                resource_id=str(entity_id),
                metadata={
                    "relation": "CATALOGUES",
                    "added": added,
                    "removed": removed,
                    "continuing_count": len(continuing),
                },
            )

        save_receipt(
            conn,
            principal,
            key,
            "MODEL_ENTITY_CATALOGUES_EXACT_SET",
            str(entity_id),
            digest,
            response,
        )
        conn.commit()
        return response
