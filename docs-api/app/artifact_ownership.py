"""Transaction-local generated-artifact ownership mutations.

These helpers never commit. MODEL endpoints and governed publication call the
same implementation so page state and generated ownership can share one commit.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg2.errors
import psycopg2.extras
from fastapi import HTTPException

from app.model_models import ArtifactSuccessor


ARTIFACT_COLUMNS = (
    "artifact_id", "artifact_key", "generator_name", "generator_version",
    "projection_contract_version", "config_hash", "source_entity_id",
    "redaction_policy", "target_page_paths", "declared_by", "status",
    "retired_at", "version", "created_at", "updated_at",
)


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def load_artifact(conn, artifact_id: str, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        "SELECT artifact_id, artifact_key, generator_name, generator_version, "
        "projection_contract_version, config_hash, source_entity_id, redaction_policy, "
        "target_page_paths, declared_by, status, retired_at, version, created_at, updated_at "
        "FROM model.generated_artifacts WHERE artifact_id = %s" + (" FOR UPDATE" if for_update else ""),
        (artifact_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
    value = dict(zip(ARTIFACT_COLUMNS, row))
    for key in ("artifact_id", "source_entity_id", "declared_by"):
        value[key] = str(value[key])
    value["uri"] = f"docplane://model/artifact/{value['artifact_key']}"
    return value


def target_ids(conn, artifact_id: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT page_resource_id::text FROM model.artifact_targets WHERE artifact_id = %s ORDER BY page_resource_id",
        (artifact_id,),
    )
    return [row[0] for row in cur.fetchall()]


def _validate_custody(artifact: dict[str, Any], principal_id: str, expected_version: int) -> None:
    if artifact["status"] != "DECLARED":
        raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_NOT_DECLARED", "status": artifact["status"]})
    if artifact["declared_by"] != principal_id:
        raise HTTPException(status_code=403, detail={"code": "MODEL_ARTIFACT_CUSTODY_FORBIDDEN"})
    if artifact["version"] != expected_version:
        raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_VERSION_STALE", "current": artifact["version"]})


def _validate_pages(conn, desired: set[str]) -> None:
    if not desired:
        return
    cur = conn.cursor()
    cur.execute("SELECT resource_id::text FROM docs.pages WHERE resource_id = ANY(%s::uuid[])", (sorted(desired),))
    found = {row[0] for row in cur.fetchall()}
    missing = sorted(desired - found)
    if missing:
        raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_TARGET_PAGE_NOT_FOUND", "missing": missing})


def _validate_target_paths(conn, desired_ids: list[str], desired_paths: list[str]) -> None:
    if not desired_ids:
        return
    expected = dict(zip(desired_ids, desired_paths))
    cur = conn.cursor()
    cur.execute("SELECT resource_id::text, path FROM docs.pages WHERE resource_id = ANY(%s::uuid[])", (desired_ids,))
    mismatches = [
        {"page_resource_id": resource_id, "expected_path": expected[resource_id], "current_path": path}
        for resource_id, path in cur.fetchall() if expected[resource_id] != path
    ]
    if mismatches:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ARTIFACT_TARGET_PATH_MISMATCH", "mismatches": mismatches})


def _validate_removals_archived(conn, removed: set[str]) -> None:
    if not removed:
        return
    cur = conn.cursor()
    cur.execute("SELECT resource_id::text, status FROM docs.pages WHERE resource_id = ANY(%s::uuid[])", (sorted(removed),))
    active = sorted(row[0] for row in cur.fetchall() if row[1] != "archived")
    if active:
        raise HTTPException(
            status_code=409,
            detail={"code": "GENERATED_TARGET_REMOVAL_REQUIRES_ARCHIVE", "page_resource_ids": active},
        )


def _validate_conflicts(conn, desired: set[str], allowed_artifacts: set[str]) -> None:
    if not desired:
        return
    cur = conn.cursor()
    cur.execute(
        "SELECT page_resource_id::text, artifact_id::text FROM model.artifact_targets "
        "WHERE page_resource_id = ANY(%s::uuid[]) AND NOT (artifact_id = ANY(%s::uuid[]))",
        (sorted(desired), sorted(allowed_artifacts)),
    )
    conflicts = [{"page_resource_id": row[0], "artifact_id": row[1]} for row in cur.fetchall()]
    if conflicts:
        raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_TARGET_CONFLICT", "conflicts": conflicts})


def reconcile_targets(
    conn,
    *,
    artifact_id: str,
    expected_version: int,
    desired_ids: list[str],
    desired_paths: list[str],
    generator_version: str,
    principal_id: str,
) -> dict[str, Any]:
    artifact = load_artifact(conn, artifact_id, for_update=True)
    _validate_custody(artifact, principal_id, expected_version)
    desired = set(desired_ids)
    current = set(target_ids(conn, artifact_id))
    _validate_pages(conn, desired)
    _validate_target_paths(conn, desired_ids, desired_paths)
    _validate_conflicts(conn, desired, {artifact_id})
    removed, added = current - desired, desired - current
    _validate_removals_archived(conn, removed)
    changed = bool(removed or added or sorted(artifact["target_page_paths"]) != sorted(desired_paths) or artifact["generator_version"] != generator_version)
    if not changed:
        return {"artifact": artifact, "changed": False, "added": [], "removed": [], "continuing": sorted(current)}
    cur = conn.cursor()
    if removed:
        cur.execute(
            "DELETE FROM model.artifact_targets WHERE artifact_id = %s AND page_resource_id = ANY(%s::uuid[])",
            (artifact_id, sorted(removed)),
        )
    for page_id in sorted(added):
        try:
            cur.execute("INSERT INTO model.artifact_targets (artifact_id, page_resource_id) VALUES (%s, %s)", (artifact_id, page_id))
        except psycopg2.errors.UniqueViolation as exc:
            raise HTTPException(status_code=409, detail={"code": "MODEL_ARTIFACT_TARGET_CONFLICT", "page_resource_id": page_id}) from exc
    if added:
        cur.execute("UPDATE docs.pages SET provenance = 'GENERATED', updated_at = now() WHERE resource_id = ANY(%s::uuid[])", (sorted(added),))
    cur.execute(
        "UPDATE model.generated_artifacts SET generator_version = %s, target_page_paths = %s, version = version + 1, updated_at = now() WHERE artifact_id = %s",
        (generator_version, _json(sorted(desired_paths)), artifact_id),
    )
    return {
        "artifact": load_artifact(conn, artifact_id), "changed": True,
        "added": sorted(added), "removed": sorted(removed), "continuing": sorted(current & desired),
    }


def handoff_targets(
    conn,
    *,
    predecessor_id: str,
    expected_version: int,
    successor: ArtifactSuccessor,
    principal_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    predecessor = load_artifact(conn, predecessor_id, for_update=True)
    _validate_custody(predecessor, principal_id, expected_version)
    if successor.artifact_key != predecessor["artifact_key"]:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ARTIFACT_SUCCESSOR_KEY_MISMATCH"})
    if successor.projection_contract_version <= predecessor["projection_contract_version"]:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ARTIFACT_SUCCESSOR_CONTRACT_VERSION_INVALID", "current": predecessor["projection_contract_version"]})
    desired = {str(item) for item in successor.target_page_resource_ids}
    current = set(target_ids(conn, predecessor_id))
    _validate_pages(conn, desired)
    _validate_target_paths(
        conn,
        [str(item) for item in successor.target_page_resource_ids],
        successor.target_page_paths,
    )
    _validate_conflicts(conn, desired, {predecessor_id})
    removed, added, continuing = current - desired, desired - current, current & desired
    _validate_removals_archived(conn, removed)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM model.entities WHERE entity_id = %s", (str(successor.source_entity_id),))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_ENTITY_NOT_FOUND"})
    # Retiring inside this transaction releases only the active-key index. The
    # target rows remain owned until they are transferred below, and rollback
    # restores the predecessor if any successor validation/mutation fails.
    cur.execute(
        "UPDATE model.generated_artifacts SET status = 'RETIRED', retired_at = now(), version = version + 1, updated_at = now() WHERE artifact_id = %s",
        (predecessor_id,),
    )
    cur.execute(
        """
        INSERT INTO model.generated_artifacts
            (artifact_key, generator_name, generator_version, projection_contract_version,
             config_hash, source_entity_id, redaction_policy, target_page_paths,
             declared_by, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING artifact_id::text
        """,
        (successor.artifact_key, successor.generator_name, successor.generator_version,
         successor.projection_contract_version, successor.config_hash,
         str(successor.source_entity_id), successor.redaction_policy,
         _json(sorted(successor.target_page_paths)), principal_id, idempotency_key),
    )
    successor_id = cur.fetchone()[0]
    # Continuing rows change owner directly; no committed zero-owner interval.
    if continuing:
        cur.execute(
            "UPDATE model.artifact_targets SET artifact_id = %s WHERE artifact_id = %s AND page_resource_id = ANY(%s::uuid[])",
            (successor_id, predecessor_id, sorted(continuing)),
        )
    if removed:
        cur.execute("DELETE FROM model.artifact_targets WHERE artifact_id = %s AND page_resource_id = ANY(%s::uuid[])", (predecessor_id, sorted(removed)))
    for page_id in sorted(added):
        cur.execute("INSERT INTO model.artifact_targets (artifact_id, page_resource_id) VALUES (%s, %s)", (successor_id, page_id))
    if added:
        cur.execute("UPDATE docs.pages SET provenance = 'GENERATED', updated_at = now() WHERE resource_id = ANY(%s::uuid[])", (sorted(added),))
    # Operational execution declarations survive projection-contract succession.
    cur.execute(
        """
        INSERT INTO model.generated_artifact_execution_contracts
            (artifact_id, contract_schema_version, observation_owner_principal_id,
             observation_trigger, observation_max_age_seconds,
             generation_owner_principal_id, generation_trigger, exclusion_domain,
             created_by, updated_by)
        SELECT %s, contract_schema_version, observation_owner_principal_id,
               observation_trigger, observation_max_age_seconds,
               generation_owner_principal_id, generation_trigger, exclusion_domain,
               %s, %s
          FROM model.generated_artifact_execution_contracts WHERE artifact_id = %s
        """,
        (successor_id, principal_id, principal_id, predecessor_id),
    )
    return {
        "predecessor": load_artifact(conn, predecessor_id), "successor": load_artifact(conn, successor_id),
        "added": sorted(added), "removed": sorted(removed), "continuing": sorted(continuing), "changed": True,
    }
