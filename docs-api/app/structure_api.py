"""Structured schema projection: generator custody writes and the agent read surface.

Initiative e004787b, decision D1 (2026-09-26): the detailed schema graph —
tables, columns, keys, constraints, indexes and relations — lives in a
generator-owned structured projection attached to the generated artifact, not
as TABLE/COLUMN MODEL entities. DATABASE and SCHEMA stay the MODEL identities.

Write side
  ``PUT /api/v1/model/artifacts/{artifact_id}/projection`` — only the artifact's
  declaring AUTOMATION principal, only while DECLARED. The API re-derives the
  structural fingerprint from ``document.schemas`` and refuses a mismatch, so a
  stored projection provably corresponds to the GENERATION evidence that names
  that fingerprint. Content is scanned by the canonical redaction transform and
  refused (never silently altered) if any value is secret-shaped.

Read side (agents; contributor bearer)
  ``/api/v1/model/structure``                              databases
  ``/api/v1/model/structure/{db}``                         provenance + schemas
  ``/api/v1/model/structure/{db}/{schema}``                tables/views/enums
  ``/api/v1/model/structure/{db}/{schema}/{table}``        one table + relations
  ``/api/v1/model/structure/{db}/{schema}/{table}/{col}``  one column in context
  ``/api/v1/model/structure-columns?name=``                find a column anywhere
  ``/api/v1/model/structure-relations/{db}/{schema}/{table}`` FK neighbourhood/path

Every response carries a stable ``uri`` and the projection identity
(fingerprint, contract version, publication time) plus derived freshness, so an
agent always knows what it read and how current it is. Reads never mutate.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, model_validator

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt
from app.observe_api import (
    _artifact_execution_contract,
    _latest_generation,
    _latest_source_observation,
    derive_freshness,
)
from migration.redaction import DocumentRefusedError, MalformedMarkerError, redact

router = APIRouter(tags=["model-structure-v1"])

PROJECTION_KIND = "SCHEMA_STRUCTURE"
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_HEX64 = r"^[0-9a-f]{64}$"
_DIGEST_FIELDS = ("source_fingerprint", "config_hash")


def canonical_sha256(value: Any) -> str:
    """The schema-catalogue fingerprint algorithm (schema_catalogue_source)."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactProjectionPut(BaseModel):
    projection_kind: Literal["SCHEMA_STRUCTURE"]
    projection_contract_version: int = Field(ge=1, le=2147483647)
    source_fingerprint: str = Field(pattern=_HEX64)
    config_hash: str = Field(pattern=_HEX64)
    document: dict[str, Any]
    provenance: dict[str, Any]

    @model_validator(mode="after")
    def coherent(self):
        size = len(json.dumps(self.document, sort_keys=True, ensure_ascii=False, default=str))
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
        if len(json.dumps(self.provenance, sort_keys=True, default=str)) > 60000:
            raise ValueError("provenance exceeds 60000 bytes")
        schemas = self.document.get("schemas")
        if not isinstance(schemas, dict) or not schemas:
            raise ValueError("document.schemas must be a non-empty object")
        for schema, body in schemas.items():
            if not isinstance(body, dict) or not all(
                isinstance(body.get(part), dict) for part in ("tables", "views", "enums")
            ):
                raise ValueError(f"document.schemas.{schema} must carry tables, views and enums")
        if not isinstance(self.document.get("relations"), list):
            raise ValueError("document.relations must be a list")
        for field in (*_DIGEST_FIELDS, "projection_contract_version"):
            if self.document.get(field) != getattr(self, field):
                raise ValueError(f"document.{field} must equal the request {field}")
        return self


def _string_leaves(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item)
    elif isinstance(value, str):
        yield value


def projection_secret_findings(document: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail-closed canonical scan of every string an agent could read.

    Per leaf rather than per serialised blob (JSON punctuation next to prose
    otherwise forms false malformed-marker tokens); the two generator-computed
    SHA-256 identities are shape-validated by the request model instead,
    because 64 hex characters is exactly what HEX_SECRET exists to catch.
    Findings name a location and class, never the value.
    """
    findings: list[dict[str, Any]] = []
    scanned = {key: value for key, value in document.items() if key not in _DIGEST_FIELDS}
    for label, root in (("document", scanned), ("provenance", provenance)):
        for text in _string_leaves(root):
            try:
                result = redact(text, label="PROJECTION")
            except MalformedMarkerError:
                findings.append({"part": label, "code": "MALFORMED_REDACTION_MARKER"})
                continue
            except DocumentRefusedError:
                findings.append({"part": label, "code": "SECRET_SHAPED_VALUE", "classes": ["REFUSED"]})
                continue
            if result.changed:
                findings.append({"part": label, "code": "SECRET_SHAPED_VALUE", "classes": sorted(set(result.findings))})
    return findings[:20]


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _projection_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": row["artifact_id"],
        "artifact_key": row.get("artifact_key"),
        "projection_kind": row["projection_kind"],
        "projection_contract_version": row["projection_contract_version"],
        "source_fingerprint": row["source_fingerprint"],
        "config_hash": row["config_hash"],
        "document_sha256": row["document_sha256"],
        "version": row["version"],
        "published_at": row["updated_at"],
        "published_by": row["published_by"],
    }


_PROJECTION_COLUMNS = (
    "artifact_id", "projection_kind", "projection_contract_version", "source_fingerprint",
    "config_hash", "document_sha256", "published_by", "version", "created_at", "updated_at",
)


def _load_projection_row(cur, artifact_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT artifact_id::text, projection_kind, projection_contract_version, source_fingerprint, "
        "config_hash, document_sha256, published_by::text, version, created_at, updated_at "
        "FROM model.artifact_projections WHERE artifact_id = %s",
        (artifact_id,),
    )
    row = cur.fetchone()
    return dict(zip(_PROJECTION_COLUMNS, row)) if row else None


@router.put("/api/v1/model/artifacts/{artifact_id}/projection")
def put_artifact_projection(
    artifact_id: UUID,
    request: ArtifactProjectionPut,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "artifact-projection-put", "artifact_id": str(artifact_id), **request.model_dump(mode="json")})
    derived = canonical_sha256(request.document["schemas"])
    if derived != request.source_fingerprint:
        raise HTTPException(status_code=422, detail={
            "code": "PROJECTION_FINGERPRINT_MISMATCH",
            "message": "document.schemas does not hash to source_fingerprint under the schema-catalogue algorithm.",
        })
    findings = projection_secret_findings(request.document, request.provenance)
    if findings:
        raise HTTPException(status_code=422, detail={"code": "PROJECTION_SECRET_SHAPED", "findings": findings})
    document_sha256 = canonical_sha256(request.document)
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "MODEL_ARTIFACT_PROJECTION_PUT", digest)
        if replayed is not None:
            return replayed
        cur = conn.cursor()
        cur.execute(
            """
            SELECT a.status, a.declared_by::text, e.entity_kind, e.entity_key
              FROM model.generated_artifacts a
              JOIN model.entities e ON e.entity_id = a.source_entity_id
             WHERE a.artifact_id = %s
               FOR UPDATE OF a
            """,
            (str(artifact_id),),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
        status, declared_by, entity_kind, entity_key = row
        if status != "DECLARED":
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_NOT_DECLARED", "status": status})
        if declared_by != str(principal.principal_id):
            raise HTTPException(status_code=403, detail={"code": "MODEL_ARTIFACT_PROJECTION_FORBIDDEN",
                                                         "message": "Only the artifact's declaring generator may write its projection."})
        if entity_kind != "DATABASE" or (request.document.get("database") or {}).get("key") != entity_key:
            raise HTTPException(status_code=422, detail={"code": "PROJECTION_SOURCE_MISMATCH",
                                                         "message": "document.database.key must name the artifact's DATABASE source entity."})
        existing = _load_projection_row(cur, str(artifact_id))
        changed = not (
            existing is not None
            and existing["document_sha256"] == document_sha256
            and existing["projection_contract_version"] == request.projection_contract_version
        )
        if changed:
            cur.execute(
                """
                INSERT INTO model.artifact_projections
                    (artifact_id, projection_kind, projection_contract_version, source_fingerprint,
                     config_hash, document_sha256, document, provenance, published_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT (artifact_id) DO UPDATE SET
                    projection_kind = EXCLUDED.projection_kind,
                    projection_contract_version = EXCLUDED.projection_contract_version,
                    source_fingerprint = EXCLUDED.source_fingerprint,
                    config_hash = EXCLUDED.config_hash,
                    document_sha256 = EXCLUDED.document_sha256,
                    document = EXCLUDED.document,
                    provenance = EXCLUDED.provenance,
                    published_by = EXCLUDED.published_by,
                    version = model.artifact_projections.version + 1
                """,
                (
                    str(artifact_id), request.projection_kind, request.projection_contract_version,
                    request.source_fingerprint, request.config_hash, document_sha256,
                    json.dumps(request.document, sort_keys=True, ensure_ascii=False, default=str),
                    json.dumps(request.provenance, sort_keys=True, ensure_ascii=False, default=str),
                    principal.principal_id,
                ),
            )
            append_event(
                conn,
                event_type="MODEL_ARTIFACT_PROJECTION_PUBLISHED",
                channel="API",
                producer_id="docplane-model",
                idempotency_key=f"MODEL_ARTIFACT_PROJECTION_PUBLISHED:{principal.principal_id}:{key}",
                principal=principal,
                resource_type="MODEL_ARTIFACT",
                resource_id=str(artifact_id),
                metadata={
                    "projection_kind": request.projection_kind,
                    "projection_contract_version": request.projection_contract_version,
                    "source_fingerprint_prefix": request.source_fingerprint[:16],
                },
            )
        stored = _load_projection_row(cur, str(artifact_id))
        response = jsonable_encoder({"changed": changed, "projection": _projection_summary(stored)})
        save_receipt(conn, principal, key, "MODEL_ARTIFACT_PROJECTION_PUT", str(artifact_id), digest, response)
        conn.commit()
        return response


@router.get("/api/v1/model/artifacts/{artifact_id}/projection")
def get_artifact_projection(
    artifact_id: UUID,
    include_document: bool = Query(default=False),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        row = _load_projection_row(cur, str(artifact_id))
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_PROJECTION_NOT_FOUND"})
        value = _projection_summary(row)
        if include_document:
            cur.execute("SELECT document, provenance FROM model.artifact_projections WHERE artifact_id = %s", (str(artifact_id),))
            value["document"], value["provenance"] = cur.fetchone()
    return jsonable_encoder(value)


# ── Read surface ────────────────────────────────────────────────────────────

def _uri(db_key: str, schema: str | None = None, table: str | None = None, column: str | None = None) -> str:
    uri = f"docplane://model/database/{db_key}"
    if schema is not None:
        uri += f"/schema/{schema}"
    if table is not None:
        uri += f"/table/{table}"
    if column is not None:
        uri += f"/column/{column}"
    return uri


_ACTIVE_SQL = """
SELECT e.entity_id::text, e.entity_key, e.display_name, a.artifact_id::text, a.artifact_key,
       p.projection_kind, p.projection_contract_version, p.source_fingerprint, p.config_hash,
       p.document_sha256, p.published_by::text, p.version, p.created_at, p.updated_at,
       p.provenance
  FROM model.entities e
  JOIN model.generated_artifacts a ON a.source_entity_id = e.entity_id AND a.status = 'DECLARED'
  JOIN model.artifact_projections p ON p.artifact_id = a.artifact_id AND p.projection_kind = 'SCHEMA_STRUCTURE'
 WHERE e.entity_kind = 'DATABASE' AND e.status = 'ACTIVE'
"""
_ACTIVE_COLUMNS = (
    "entity_id", "db_key", "display_name", "artifact_id", "artifact_key",
    "projection_kind", "projection_contract_version", "source_fingerprint", "config_hash",
    "document_sha256", "published_by", "version", "created_at", "updated_at", "provenance",
)


def _active(cur, db_key: str | None = None) -> list[dict[str, Any]]:
    sql = _ACTIVE_SQL + (" AND e.entity_key = %s" if db_key is not None else "") + " ORDER BY e.entity_key, p.updated_at DESC"
    cur.execute(sql, (db_key,) if db_key is not None else ())
    seen: set[str] = set()
    rows = []
    for row in cur.fetchall():
        value = dict(zip(_ACTIVE_COLUMNS, row))
        if value["db_key"] in seen:
            continue
        seen.add(value["db_key"])
        rows.append(value)
    return rows


def _one(cur, db_key: str) -> dict[str, Any]:
    rows = _active(cur, db_key)
    if not rows:
        raise HTTPException(status_code=404, detail={
            "code": "STRUCTURE_DATABASE_NOT_FOUND",
            "message": "No published schema projection for this DATABASE key.",
            "remedy": "GET /api/v1/model/structure lists catalogued databases.",
        })
    return rows[0]


def _freshness(cur, active: dict[str, Any]) -> dict[str, Any]:
    execution_contract, _ = _artifact_execution_contract(cur, active["artifact_id"])
    freshness = derive_freshness(
        _latest_generation(cur, active["artifact_id"]),
        _latest_generation(cur, active["artifact_id"], successful_only=True),
        _latest_source_observation(cur, active["entity_id"]),
        latest_successful_source_observation=_latest_source_observation(cur, active["entity_id"], successful_only=True),
        execution_contract=execution_contract,
    )
    # Compact, agent-facing view of the canonical derivation, plus the two
    # correspondences only this surface can state: does the structure being
    # served equal the last successful generation, and the live source?
    served = active["source_fingerprint"]
    live = freshness.get("source_fingerprint")
    return {
        "state": freshness.get("state"),
        "reason": freshness.get("reason"),
        "projection_correspondence": freshness.get("projection_correspondence"),
        "source_observation_status": freshness.get("source_observation_status"),
        "observation_expires_at": freshness.get("observation_expires_at"),
        "last_source_observation_at": (freshness.get("source_observation") or {}).get("observed_at"),
        "last_generation_at": freshness.get("observed_at"),
        "generated_fingerprint": freshness.get("generated_fingerprint"),
        "live_source_fingerprint": live,
        "served_matches_generation": served == freshness.get("generated_fingerprint"),
        "served_matches_live_source": None if live is None else served == live,
    }


def _identity(active: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": active["artifact_id"],
        "artifact_key": active["artifact_key"],
        "projection_contract_version": active["projection_contract_version"],
        "source_fingerprint": active["source_fingerprint"],
        "projection_version": active["version"],
        "published_at": active["updated_at"],
        "extracted_at": (active.get("provenance") or {}).get("extracted_at"),
        "environment": (active.get("provenance") or {}).get("environment"),
    }


def _json_path(cur, artifact_id: str, *path: str) -> Any:
    cur.execute(
        "SELECT document #> %s FROM model.artifact_projections WHERE artifact_id = %s",
        (list(path), artifact_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _schema_body(cur, active: dict[str, Any], schema: str) -> dict[str, Any]:
    body = _json_path(cur, active["artifact_id"], "schemas", schema)
    if body is None:
        raise HTTPException(status_code=404, detail={"code": "STRUCTURE_SCHEMA_NOT_FOUND", "database": active["db_key"], "schema": schema})
    return body


def _relation(cur, active: dict[str, Any], schema: str, table: str) -> tuple[str, dict[str, Any]]:
    body = _schema_body(cur, active, schema)
    if table in body["tables"]:
        return "table", body["tables"][table]
    if table in body["views"]:
        return "view", body["views"][table]
    raise HTTPException(status_code=404, detail={
        "code": "STRUCTURE_TABLE_NOT_FOUND", "database": active["db_key"], "schema": schema, "table": table,
        "remedy": f"GET /api/v1/model/structure/{active['db_key']}/{schema} lists its tables.",
    })


def _relations(cur, active: dict[str, Any]) -> list[dict[str, Any]]:
    return _json_path(cur, active["artifact_id"], "relations") or []


def _edge_view(edge: dict[str, Any], db_key: str) -> dict[str, Any]:
    return {
        **edge,
        "from_uri": _uri(db_key, edge["from"]["schema"], edge["from"]["table"]),
        "to_uri": _uri(db_key, edge["to"]["schema"], edge["to"]["table"]),
    }


@router.get("/api/v1/model/structure")
def list_structures(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        databases = []
        for active in _active(cur):
            provenance = active.get("provenance") or {}
            schemas = {}
            for schema, spec in (provenance.get("schemas") or {}).items():
                body = _json_path(cur, active["artifact_id"], "schemas", schema) or {}
                schemas[schema] = {
                    "uri": _uri(active["db_key"], schema),
                    "tables": len(body.get("tables") or {}),
                    "views": len(body.get("views") or {}),
                    "basis": spec.get("basis"),
                    "canonical_status": (spec.get("canonical") or {}).get("status"),
                }
            freshness = _freshness(cur, active)
            databases.append({
                "db_key": active["db_key"],
                "display_name": active["display_name"],
                "uri": _uri(active["db_key"]),
                "schemas": schemas,
                "projection": _identity(active),
                "freshness": {key: freshness.get(key) for key in ("state", "projection_correspondence", "source_observation_status", "reason")},
            })
    return jsonable_encoder({"databases": databases, "count": len(databases)})


@router.get("/api/v1/model/structure-columns")
def find_columns(
    name: str = Query(min_length=1, max_length=200),
    match: Literal["exact", "prefix", "contains"] = Query(default="exact"),
    db_key: str | None = Query(default=None, max_length=127),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    needle = name.lower()

    def hit(candidate: str) -> bool:
        value = candidate.lower()
        return value == needle if match == "exact" else value.startswith(needle) if match == "prefix" else needle in value

    matches: list[dict[str, Any]] = []
    total = 0
    with get_conn() as conn:
        cur = conn.cursor()
        for active in _active(cur, db_key):
            cur.execute("SELECT document FROM model.artifact_projections WHERE artifact_id = %s", (active["artifact_id"],))
            document = cur.fetchone()[0]
            inbound: dict[tuple[str, str, str], int] = {}
            for edge in document.get("relations", []):
                for column in edge["to"]["columns"]:
                    slot = (edge["to"]["schema"], edge["to"]["table"], column)
                    inbound[slot] = inbound.get(slot, 0) + 1
            for schema in sorted(document["schemas"]):
                body = document["schemas"][schema]
                for kind, relations in (("table", body["tables"]), ("view", body["views"])):
                    for table in sorted(relations):
                        definition = relations[table]
                        pk = set(((definition.get("primary_key") or {}).get("columns")) or [])
                        for column in definition["columns"]:
                            if not hit(column["name"]):
                                continue
                            total += 1
                            if len(matches) >= limit:
                                continue
                            fks = [
                                {"constraint": fk["name"], "references": fk["references"],
                                 "to_uri": _uri(active["db_key"], fk["references"]["schema"], fk["references"]["table"])}
                                for fk in definition.get("foreign_keys", []) if column["name"] in fk["columns"]
                            ]
                            matches.append({
                                "uri": _uri(active["db_key"], schema, table, column["name"]),
                                "db_key": active["db_key"], "schema": schema, "table": table,
                                "relation_kind": kind, "column": column["name"], "type": column["type"],
                                "nullable": column["nullable"], "primary_key": column["name"] in pk,
                                "foreign_keys": fks,
                                "referenced_by": inbound.get((schema, table, column["name"]), 0),
                            })
    return jsonable_encoder({
        "query": {"name": name, "match": match, "db_key": db_key},
        "matches": matches, "count": len(matches), "total": total, "truncated": total > len(matches),
    })


@router.get("/api/v1/model/structure-relations/{db_key}/{schema}/{table}")
def table_relations(
    db_key: str,
    schema: str,
    table: str,
    depth: int = Query(default=1, ge=1, le=4),
    to: str | None = Query(default=None, max_length=300, description="schema.table: return the shortest FK path instead of a neighbourhood"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        active = _one(cur, db_key)
        _relation(cur, active, schema, table)
        edges = _relations(cur, active)
        identity = _identity(active)
    root = f"{schema}.{table}"
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in edges:
        left = f"{edge['from']['schema']}.{edge['from']['table']}"
        right = f"{edge['to']['schema']}.{edge['to']['table']}"
        adjacency.setdefault(left, []).append((right, edge))
        adjacency.setdefault(right, []).append((left, edge))
    if to is not None:
        previous: dict[str, tuple[str, dict[str, Any]] | None] = {root: None}
        queue = deque([root])
        while queue and to not in previous:
            node = queue.popleft()
            for neighbour, edge in adjacency.get(node, []):
                if neighbour not in previous:
                    previous[neighbour] = (node, edge)
                    queue.append(neighbour)
        if to not in previous:
            return jsonable_encoder({"root": root, "to": to, "path": None, "edges": [],
                                     "message": "No foreign-key path connects these tables in the catalogued schemas.",
                                     "projection": identity})
        path_edges = []
        node = to
        while previous[node] is not None:
            parent, edge = previous[node]
            path_edges.append(_edge_view(edge, db_key))
            node = parent
        path_edges.reverse()
        nodes = [root]
        for edge in path_edges:
            left = f"{edge['from']['schema']}.{edge['from']['table']}"
            right = f"{edge['to']['schema']}.{edge['to']['table']}"
            nodes.append(right if nodes[-1] == left else left)
        return jsonable_encoder({"root": root, "to": to, "path": nodes, "edges": path_edges, "projection": identity})
    seen = {root: 0}
    queue = deque([root])
    collected: dict[str, dict[str, Any]] = {}
    while queue:
        node = queue.popleft()
        if seen[node] >= depth:
            continue
        for neighbour, edge in adjacency.get(node, []):
            collected[f"{edge['from']['schema']}.{edge['from']['table']}.{edge['constraint']}"] = edge
            if neighbour not in seen:
                seen[neighbour] = seen[node] + 1
                queue.append(neighbour)
    outbound = [_edge_view(e, db_key) for e in edges if f"{e['from']['schema']}.{e['from']['table']}" == root]
    inbound = [_edge_view(e, db_key) for e in edges if f"{e['to']['schema']}.{e['to']['table']}" == root]
    return jsonable_encoder({
        "root": root, "uri": _uri(db_key, schema, table), "depth": depth,
        "outbound": outbound, "inbound": inbound,
        "neighbourhood": {
            "tables": [{"table": name, "hops": hops, "uri": _uri(db_key, *name.split(".", 1))} for name, hops in sorted(seen.items(), key=lambda item: (item[1], item[0]))],
            "edges": [_edge_view(collected[key], db_key) for key in sorted(collected)],
        },
        "projection": identity,
    })


@router.get("/api/v1/model/structure/{db_key}")
def describe_database(db_key: str, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        active = _one(cur, db_key)
        cur.execute("SELECT document -> 'schemas', document -> 'database' FROM model.artifact_projections WHERE artifact_id = %s", (active["artifact_id"],))
        schemas_doc, database = cur.fetchone()
        freshness = _freshness(cur, active)
        cur.execute(
            "SELECT p.path FROM model.entity_page_links l JOIN docs.pages p ON p.resource_id = l.page_resource_id "
            "WHERE l.entity_id = %s AND l.relation = 'CATALOGUES' ORDER BY p.path",
            (active["entity_id"],),
        )
        pages = [row[0] for row in cur.fetchall()]
    provenance = active.get("provenance") or {}
    schemas = {
        schema: {
            "uri": _uri(db_key, schema),
            "comment": body.get("comment"),
            "tables": sorted(body["tables"]),
            "views": sorted(body["views"]),
            "enums": sorted(body["enums"]),
            **((provenance.get("schemas") or {}).get(schema) or {}),
        }
        for schema, body in sorted(schemas_doc.items())
    }
    return jsonable_encoder({
        "uri": _uri(db_key), "database": database, "entity_id": active["entity_id"],
        "schemas": schemas, "provenance": provenance, "freshness": freshness,
        "projection": _identity(active), "catalogue_pages": pages,
    })


@router.get("/api/v1/model/structure/{db_key}/{schema}")
def describe_schema(db_key: str, schema: str, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        active = _one(cur, db_key)
        body = _schema_body(cur, active, schema)
        edges = _relations(cur, active)
    inbound: dict[str, int] = {}
    for edge in edges:
        if edge["to"]["schema"] == schema:
            inbound[edge["to"]["table"]] = inbound.get(edge["to"]["table"], 0) + 1
    tables = {
        name: {
            "uri": _uri(db_key, schema, name),
            "kind": table["kind"],
            "comment": table["comment"],
            "columns": [column["name"] for column in table["columns"]],
            "primary_key": (table.get("primary_key") or {}).get("columns"),
            "references": sorted({f"{fk['references']['schema']}.{fk['references']['table']}" for fk in table["foreign_keys"]}),
            "referenced_by": inbound.get(name, 0),
        }
        for name, table in sorted(body["tables"].items())
    }
    views = {
        name: {"uri": _uri(db_key, schema, name), "kind": view["kind"], "comment": view["comment"],
               "columns": [column["name"] for column in view["columns"]]}
        for name, view in sorted(body["views"].items())
    }
    provenance = (active.get("provenance") or {}).get("schemas", {}).get(schema)
    return jsonable_encoder({
        "uri": _uri(db_key, schema), "db_key": db_key, "schema": schema, "comment": body.get("comment"),
        "provenance": provenance, "tables": tables, "views": views, "enums": body["enums"],
        "projection": _identity(active),
    })


@router.get("/api/v1/model/structure/{db_key}/{schema}/{table}")
def describe_table(db_key: str, schema: str, table: str, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        active = _one(cur, db_key)
        kind, definition = _relation(cur, active, schema, table)
        edges = _relations(cur, active)
    outbound = [_edge_view(e, db_key) for e in edges if e["from"]["schema"] == schema and e["from"]["table"] == table]
    inbound = [_edge_view(e, db_key) for e in edges if e["to"]["schema"] == schema and e["to"]["table"] == table]
    return jsonable_encoder({
        "uri": _uri(db_key, schema, table), "db_key": db_key, "schema": schema, "table": table,
        "relation_kind": kind, "definition": definition,
        "relations": {"outbound": outbound, "inbound": inbound},
        "projection": _identity(active),
    })


@router.get("/api/v1/model/structure/{db_key}/{schema}/{table}/{column}")
def describe_column(db_key: str, schema: str, table: str, column: str, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        active = _one(cur, db_key)
        kind, definition = _relation(cur, active, schema, table)
        edges = _relations(cur, active)
    found = next((item for item in definition["columns"] if item["name"] == column), None)
    if found is None:
        raise HTTPException(status_code=404, detail={
            "code": "STRUCTURE_COLUMN_NOT_FOUND", "database": db_key, "schema": schema, "table": table, "column": column,
            "columns": [item["name"] for item in definition["columns"]],
        })
    pk = definition.get("primary_key") or {}
    return jsonable_encoder({
        "uri": _uri(db_key, schema, table, column), "db_key": db_key, "schema": schema, "table": table,
        "relation_kind": kind, "column": found,
        "primary_key": column in (pk.get("columns") or []),
        "foreign_keys": [
            {**fk, "to_uri": _uri(db_key, fk["references"]["schema"], fk["references"]["table"])}
            for fk in definition.get("foreign_keys", []) if column in fk["columns"]
        ],
        "referenced_by": [
            _edge_view(edge, db_key) for edge in edges
            if edge["to"]["schema"] == schema and edge["to"]["table"] == table and column in edge["to"]["columns"]
        ],
        "unique": [item for item in definition.get("unique", []) if column in item["columns"]],
        "checks": [item for item in definition.get("checks", []) if column in (item.get("columns") or [])],
        "indexes": [item for item in definition.get("indexes", []) if column in item["columns"] or column in item["include"]],
        "projection": _identity(active),
    })
