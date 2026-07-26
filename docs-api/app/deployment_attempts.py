"""Durable deployment-attempt records for certified DocPlane releases.

Every deployment cycle is one row in ``docs.deployment_attempts``. This module is the sole
writer of those records, so transition legality is not reconstructed by callers.
"""
from __future__ import annotations

import json


TERMINAL = ("COMPLETED", "FAILED")
NON_TERMINAL = ("PINNED", "BUILDING", "VALIDATED", "PROMOTED", "RECONCILIATION_REQUIRED")

CATCH_UP = "CATCH_UP"
REDEPLOY = "REDEPLOY"
BOOTSTRAP = "BOOTSTRAP"
RECERTIFY_POLICY = "RECERTIFY_POLICY"
MODES = (CATCH_UP, REDEPLOY, BOOTSTRAP, RECERTIFY_POLICY)

_ALLOWED = {
    "PINNED": {"BUILDING", "FAILED"},
    "BUILDING": {"VALIDATED", "FAILED"},
    "VALIDATED": {"PROMOTED", "FAILED"},
    "PROMOTED": {"COMPLETED", "RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


class DeploymentAttemptError(Exception):
    def __init__(self, rule: str, message: str = ""):
        super().__init__(rule)
        self.rule = rule
        self.message = message


def _row(value):
    return dict(value) if value is not None else None


def _current_status(conn, cycle_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT status, release_id, validated_manifest_identity, sealed_digest "
        "FROM docs.deployment_attempts WHERE deployment_cycle_id = %s FOR UPDATE",
        (cycle_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise DeploymentAttemptError("ATTEMPT_NOT_FOUND", "deployment attempt not found")
    return _row(row)


def check_transition(current: str, new_status: str) -> None:
    """Raise unless ``current -> new_status`` is an allowed state-machine edge."""
    allowed = _ALLOWED.get(current)
    if allowed is None:
        raise DeploymentAttemptError(
            "ATTEMPT_STATUS_UNKNOWN", f"unknown current status {current}"
        )
    if new_status not in allowed:
        raise DeploymentAttemptError(
            "ATTEMPT_ILLEGAL_TRANSITION",
            f"attempt may not move from {current} to {new_status}",
        )


def create(
    conn,
    *,
    cycle_id,
    mode,
    target_page_identity,
    target_state_identity,
    target_state_components,
    state_identity_contract_version,
    renderer_provenance,
    publication_provenance,
    validator_contract_version,
    validation_policy_identity=None,
    validation_policy_components=None,
):
    """Insert one PINNED attempt; the database enforces one non-terminal attempt."""
    if mode not in MODES:
        raise DeploymentAttemptError("ATTEMPT_MODE_UNKNOWN", f"unsupported mode {mode}")

    import psycopg2

    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO docs.deployment_attempts
                (deployment_cycle_id, mode, status, target_page_identity,
                 target_state_identity, target_state_components,
                 state_identity_contract_version, renderer_provenance,
                 publication_provenance, validator_contract_version,
                 validation_policy_identity, validation_policy_components)
            VALUES (%s, %s, 'PINNED', %s, %s, %s::jsonb, %s,
                    %s::jsonb, %s::jsonb, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                cycle_id,
                mode,
                target_page_identity,
                target_state_identity,
                json.dumps(dict(target_state_components)),
                state_identity_contract_version,
                json.dumps(renderer_provenance or {}),
                json.dumps(publication_provenance or {}),
                validator_contract_version,
                validation_policy_identity,
                json.dumps(dict(validation_policy_components))
                if validation_policy_components
                else None,
            ),
        )
        return _row(cur.fetchone())
    except psycopg2.errors.UniqueViolation as exc:
        raise DeploymentAttemptError(
            "ATTEMPT_ALREADY_ACTIVE", "another deployment attempt is already in flight"
        ) from exc


def get(conn, cycle_id, *, for_update=False):
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM docs.deployment_attempts WHERE deployment_cycle_id = %s%s"
        % ("%s", " FOR UPDATE" if for_update else ""),
        (cycle_id,),
    )
    return _row(cur.fetchone())


def active(conn, *, for_update=False):
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM docs.deployment_attempts WHERE status = ANY(%s)%s"
        % ("%s", " FOR UPDATE" if for_update else ""),
        (list(NON_TERMINAL),),
    )
    rows = cur.fetchall()
    if len(rows) > 1:
        raise DeploymentAttemptError(
            "ATTEMPT_SINGLETON_VIOLATION",
            "more than one non-terminal deployment attempt exists",
        )
    return _row(rows[0]) if rows else None


def backfill_provenance(
    conn,
    cycle_id,
    *,
    renderer_provenance,
    publication_provenance,
    validator_contract_version,
    release_id=None,
):
    current = _current_status(conn, cycle_id)
    if current["status"] not in ("PINNED", "BUILDING"):
        raise DeploymentAttemptError(
            "ATTEMPT_PROVENANCE_LOCKED",
            "provenance may only be backfilled before validation "
            f"(status {current['status']})",
        )
    cur = conn.cursor()
    sets = [
        "renderer_provenance = %s::jsonb",
        "publication_provenance = %s::jsonb",
        "validator_contract_version = %s",
    ]
    params = [
        json.dumps(renderer_provenance or {}),
        json.dumps(publication_provenance or {}),
        validator_contract_version,
    ]
    if release_id is not None:
        sets.append("release_id = %s")
        params.append(release_id)
    params.append(cycle_id)
    cur.execute(
        "UPDATE docs.deployment_attempts SET %s WHERE deployment_cycle_id = %s RETURNING *"
        % (", ".join(sets), "%s"),
        params,
    )
    return _row(cur.fetchone())


def advance_status(
    conn,
    cycle_id,
    new_status,
    *,
    release_id=None,
    promoted=False,
    validated_manifest_identity=None,
    sealed_digest=None,
):
    current = _current_status(conn, cycle_id)
    check_transition(current["status"], new_status)

    effective_release = release_id if release_id is not None else current["release_id"]
    if new_status == "VALIDATED":
        if not effective_release:
            raise DeploymentAttemptError(
                "ATTEMPT_VALIDATED_WITHOUT_RELEASE",
                "an attempt cannot be validated without a built release",
            )
        if not validated_manifest_identity or not sealed_digest:
            raise DeploymentAttemptError(
                "ATTEMPT_VALIDATED_WITHOUT_SEAL",
                "validation must bind the manifest identity and seal digest",
            )

    cur = conn.cursor()
    sets = ["status = %s"]
    params = [new_status]
    if release_id is not None:
        sets.append("release_id = %s")
        params.append(release_id)
    if validated_manifest_identity is not None:
        sets.append("validated_manifest_identity = %s")
        params.append(validated_manifest_identity)
    if sealed_digest is not None:
        sets.append("sealed_digest = %s")
        params.append(sealed_digest)
    if promoted:
        sets.append("promoted_at = now()")
    params.append(cycle_id)
    cur.execute(
        "UPDATE docs.deployment_attempts SET %s WHERE deployment_cycle_id = %s RETURNING *"
        % (", ".join(sets), "%s"),
        params,
    )
    return _row(cur.fetchone())


def mark_completed(conn, cycle_id):
    current = _current_status(conn, cycle_id)
    check_transition(current["status"], "COMPLETED")
    cur = conn.cursor()
    cur.execute(
        "UPDATE docs.deployment_attempts "
        "SET status = 'COMPLETED', completed_at = now() "
        "WHERE deployment_cycle_id = %s RETURNING *",
        (cycle_id,),
    )
    return _row(cur.fetchone())


def mark_failed(conn, cycle_id, failure_rules):
    current = _current_status(conn, cycle_id)
    check_transition(current["status"], "FAILED")
    rules = sorted({str(rule) for rule in (failure_rules or ()) if rule})
    if not rules:
        rules = ["DEPLOYMENT_FAILED_UNSPECIFIED"]
    cur = conn.cursor()
    cur.execute(
        "UPDATE docs.deployment_attempts SET status = 'FAILED', failure_rules = %s "
        "WHERE deployment_cycle_id = %s RETURNING *",
        (rules, cycle_id),
    )
    return _row(cur.fetchone())


def mark_reconciliation_required(conn, cycle_id, reasons):
    current = _current_status(conn, cycle_id)
    check_transition(current["status"], "RECONCILIATION_REQUIRED")
    rules = sorted({str(rule) for rule in (reasons or ()) if rule}) or [
        "RECONCILIATION_REQUIRED"
    ]
    cur = conn.cursor()
    cur.execute(
        "UPDATE docs.deployment_attempts "
        "SET status = 'RECONCILIATION_REQUIRED', failure_rules = %s "
        "WHERE deployment_cycle_id = %s RETURNING *",
        (rules, cycle_id),
    )
    return _row(cur.fetchone())
