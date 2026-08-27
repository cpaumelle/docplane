"""Pure generated-artifact condition derivation.

This module projects existing MODEL/OBSERVE state into deterministic condition
proposals.  It deliberately has no API client and cannot write WORK.  Family
generators provide their structured desired CATALOGUES sets; rendered Markdown
is never inspected to infer semantic relationships.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping, Sequence


CONDITION_KINDS = (
    "DRIFTED",
    "GENERATION_FAILED",
    "SOURCE_OBSERVATION_MISSING",
    "SOURCE_OBSERVATION_FAILED",
    "SOURCE_OBSERVATION_EXPIRED",
    "EXECUTION_CONTRACT_MISSING",
    "EXECUTION_OWNER_INACTIVE",
    "CATALOGUES_MISSING",
    "CATALOGUES_DRIFTED",
)
_SAMPLE_LIMIT = 10
_STRING_MAX_UTF8_BYTES = 256
_BRIEFING_MAX_BYTES = 8192


@dataclass(frozen=True)
class CatalogueTarget:
    page_resource_id: str | None
    path: str
    status: str | None = "active"


@dataclass(frozen=True)
class EntityCatalogueState:
    entity_id: str
    entity_kind: str
    entity_status: str
    desired_targets: tuple[CatalogueTarget, ...]
    actual_page_resource_ids: tuple[str, ...]


def _encoded_size(value: Any) -> int:
    # Match the #162 receiver exactly: changing separators here could approve a
    # payload that the WORK boundary subsequently rejects.
    return len(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))


def _normalise_bounded(value: Any, path: str, truncated: list[str]) -> Any:
    """Bound untrusted identity strings without splitting them ambiguously."""
    if isinstance(value, str) and len(value.encode("utf-8")) > _STRING_MAX_UTF8_BYTES:
        truncated.append(path)
        return {"omitted": True, "original_utf8_bytes": len(value.encode("utf-8"))}
    if isinstance(value, dict):
        return {
            key: _normalise_bounded(item, f"{path}.{key}" if path else key, truncated)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [
            _normalise_bounded(item, f"{path}[{index}]", truncated)
            for index, item in enumerate(value)
        ]
    return value


def _bounded_briefing(briefing: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic receiver-safe evidence no larger than 8,192 bytes.

    Samples are diagnostic only.  Counts and allowlisted state remain durable
    even when identity samples must be omitted to satisfy WORK's hard boundary.
    """
    original_size = _encoded_size(briefing)
    truncated_fields: list[str] = []
    bounded = _normalise_bounded(briefing, "", truncated_fields)
    samples = list(bounded.get("samples", []))
    if "samples" in bounded:
        bounded["sample_count_total"] = len(samples)
        bounded["sample_count_included"] = len(samples)
        bounded["samples_truncated"] = False
    if truncated_fields:
        bounded["briefing_truncation"] = {
            "long_field_count": len(truncated_fields),
            "long_field_paths": truncated_fields[:_SAMPLE_LIMIT],
            "long_field_paths_omitted": max(0, len(truncated_fields) - _SAMPLE_LIMIT),
            "original_utf8_bytes": original_size,
        }

    while _encoded_size(bounded) > _BRIEFING_MAX_BYTES and bounded.get("samples"):
        bounded["samples"].pop()
        bounded["sample_count_included"] = len(bounded["samples"])
        bounded["samples_truncated"] = True

    if _encoded_size(bounded) > _BRIEFING_MAX_BYTES:
        # Defensive final bound for future adapters: retain deterministic scalar
        # state/counts and explicitly report that detail was omitted.
        retained = {
            key: value
            for key, value in bounded.items()
            if not isinstance(value, (dict, list)) or key == "briefing_truncation"
        }
        retained.pop("samples", None)
        retained["sample_count_included"] = 0
        retained["samples_truncated"] = bool(samples)
        retained["briefing_truncation"] = {
            "content_omitted": True,
            "original_utf8_bytes": original_size,
            "long_field_count": len(truncated_fields),
        }
        bounded = retained
    if _encoded_size(bounded) > _BRIEFING_MAX_BYTES:  # pragma: no cover - invariant guard
        raise ValueError("generated condition briefing cannot be bounded")
    return bounded


def _observation_ref(observation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only allowlisted, non-prose observation evidence."""
    if not observation:
        return {}
    # Fingerprints are already identified by the observation reference and are
    # intentionally omitted: raw digests violate WORK's secret-shaped policy.
    allowed = ("observation_id", "observed_at", "outcome")
    return {key: observation[key] for key in allowed if observation.get(key) is not None}


def _condition(
    artifact_id: str,
    artifact_key: str,
    kind: str,
    reason: str,
    **briefing: Any,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_key": artifact_key,
        "condition_kind": kind,
        "briefing": _bounded_briefing({"reason": reason, **briefing}),
    }


def _catalogues_conditions(
    artifact_id: str,
    artifact_key: str,
    entities: Sequence[EntityCatalogueState],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    drifted: list[dict[str, Any]] = []
    desired_link_count = 0
    actual_link_count = 0

    for entity in sorted(entities, key=lambda item: item.entity_id):
        desired_ids = sorted(
            target.page_resource_id
            for target in entity.desired_targets
            if target.page_resource_id is not None
        )
        unresolved = sorted(
            target.path
            for target in entity.desired_targets
            if target.page_resource_id is None or target.status != "active"
        )
        actual_ids = sorted(set(entity.actual_page_resource_ids))
        desired_link_count += len(desired_ids)
        actual_link_count += len(actual_ids)

        if entity.desired_targets and (not actual_ids or unresolved):
            missing.append(
                {
                    "entity_id": entity.entity_id,
                    "entity_kind": entity.entity_kind,
                    "missing_page_resource_ids": sorted(set(desired_ids) - set(actual_ids)),
                    # Paths may contain long hash-shaped source components and
                    # are unnecessary in WORK once entity/page identities and
                    # aggregate counts are present.
                    "unresolved_target_count": len(unresolved),
                }
            )
        # Non-empty unequal sets are drift.  RETIRED entities supplied by the
        # meter adapter have an exact empty desired set, so stale links are also
        # classified here without inventing a separate lifecycle condition.
        if actual_ids and actual_ids != desired_ids:
            drifted.append(
                {
                    "entity_id": entity.entity_id,
                    "entity_kind": entity.entity_kind,
                    "entity_status": entity.entity_status,
                    "extra_page_resource_ids": sorted(set(actual_ids) - set(desired_ids)),
                    "absent_page_resource_ids": sorted(set(desired_ids) - set(actual_ids)),
                }
            )

    common = {
        "entity_count": len(entities),
        "desired_link_count": desired_link_count,
        "actual_link_count": actual_link_count,
    }
    result: list[dict[str, Any]] = []
    if missing:
        result.append(
            _condition(
                artifact_id,
                artifact_key,
                "CATALOGUES_MISSING",
                "CATALOGUES_DESIRED_TARGET_ABSENT",
                **common,
                affected_entity_count=len(missing),
                unresolved_target_count=sum(item["unresolved_target_count"] for item in missing),
                samples=missing[:_SAMPLE_LIMIT],
            )
        )
    if drifted:
        result.append(
            _condition(
                artifact_id,
                artifact_key,
                "CATALOGUES_DRIFTED",
                "CATALOGUES_EXACT_SET_MISMATCH",
                **common,
                affected_entity_count=len(drifted),
                retired_entity_count=sum(item["entity_status"] == "RETIRED" for item in drifted),
                samples=drifted[:_SAMPLE_LIMIT],
            )
        )
    return result


def derive_generated_artifact_conditions(
    artifact_status: Mapping[str, Any],
    catalogues: Sequence[EntityCatalogueState],
    *,
    execution_owner_statuses: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Derive the complete current v1 condition set without side effects."""
    artifact = artifact_status.get("artifact") or artifact_status
    artifact_id = str(artifact["artifact_id"])
    artifact_key = str(artifact["artifact_key"])
    freshness = artifact_status.get("freshness") or {}
    state = freshness.get("state")
    reason = freshness.get("reason")
    generation = freshness.get("generation")
    source = freshness.get("source_observation")
    conditions: list[dict[str, Any]] = []

    # OBSERVE deliberately retains projection_correspondence=MISMATCH from the
    # latest successful source evidence when a newer FAILED/UNKNOWN/DEGRADED
    # check cannot supply a usable fingerprint.  The unusable check withdraws
    # FRESH but does not erase already-proven drift.  Conversely, OBSERVE emits
    # UNKNOWN correspondence when no successful source evidence supports it.
    if (state == "DRIFTED" and reason == "SOURCE_CHANGED") or freshness.get(
        "projection_correspondence"
    ) == "MISMATCH":
        conditions.append(
            _condition(
                artifact_id, artifact_key, "DRIFTED", "SOURCE_CHANGED",
                state="DRIFTED",
                projection_correspondence="MISMATCH",
                generation=_observation_ref(generation),
                source_observation=_observation_ref(source),
            )
        )
    if state == "FAILED" and reason == "LATEST_GENERATION_FAILED" and generation and generation.get("outcome") == "FAILED":
        conditions.append(
            _condition(
                artifact_id, artifact_key, "GENERATION_FAILED", reason,
                state=state,
                generation=_observation_ref(generation),
            )
        )
    if reason == "SOURCE_UNOBSERVED" and source is None:
        conditions.append(
            _condition(
                artifact_id, artifact_key, "SOURCE_OBSERVATION_MISSING", reason,
                source_observation_status=freshness.get("source_observation_status", "ABSENT"),
            )
        )
    if reason == "SOURCE_OBSERVATION_FAILED" and source and source.get("outcome") == "FAILED":
        conditions.append(
            _condition(
                artifact_id, artifact_key, "SOURCE_OBSERVATION_FAILED", reason,
                source_observation=_observation_ref(source),
                last_successful_source_observation=_observation_ref(
                    freshness.get("last_successful_source_observation")
                ),
            )
        )

    contract = artifact_status.get("execution_contract")
    if contract is not None and freshness.get("source_observation_status") == "EXPIRED":
        conditions.append(
            _condition(
                artifact_id, artifact_key, "SOURCE_OBSERVATION_EXPIRED", "SOURCE_OBSERVATION_EXPIRED",
                source_observation=_observation_ref(source),
                observation_expires_at=freshness.get("observation_expires_at"),
                observation_max_age_seconds=contract.get("observation_max_age_seconds"),
                observation_owner_principal_id=contract.get("observation_owner_principal_id"),
            )
        )

    contract_status = artifact_status.get("execution_contract_status")
    if contract_status == "UNDECLARED_TRANSITIONAL":
        conditions.append(
            _condition(
                artifact_id, artifact_key, "EXECUTION_CONTRACT_MISSING", contract_status,
                execution_contract_status=contract_status,
            )
        )
    elif contract_status == "OWNER_INACTIVE":
        owners = []
        for role, field in (
            ("OBSERVATION", "observation_owner_principal_id"),
            ("GENERATION", "generation_owner_principal_id"),
        ):
            principal_id = (contract or {}).get(field)
            status = (execution_owner_statuses or {}).get(str(principal_id))
            if principal_id is not None and status is not None and status != "ACTIVE":
                owners.append({"role": role, "principal_id": str(principal_id), "status": status})
        conditions.append(
            _condition(
                artifact_id, artifact_key, "EXECUTION_OWNER_INACTIVE", contract_status,
                execution_contract_status=contract_status,
                inactive_owners=owners,
            )
        )

    conditions.extend(_catalogues_conditions(artifact_id, artifact_key, catalogues))
    return sorted(conditions, key=lambda item: item["condition_kind"])


def work_catalogues_state(
    *,
    system_entity_id: str,
    index_target: CatalogueTarget,
    actual_page_resource_ids: Iterable[str],
) -> list[EntityCatalogueState]:
    return [
        EntityCatalogueState(
            system_entity_id, "SYSTEM", "ACTIVE", (index_target,), tuple(actual_page_resource_ids)
        )
    ]


def schema_catalogues_state(
    *,
    database_entity_id: str,
    index_target: CatalogueTarget,
    schemas: Iterable[Mapping[str, Any]],
    actual_by_entity: Mapping[str, Iterable[str]],
) -> list[EntityCatalogueState]:
    result = [
        EntityCatalogueState(
            database_entity_id,
            "DATABASE",
            "ACTIVE",
            (index_target,),
            tuple(actual_by_entity.get(database_entity_id, ())),
        )
    ]
    for schema in sorted(schemas, key=lambda item: str(item["entity_id"])):
        schema_status = str(schema.get("status"))
        target = schema["catalogue_target"]
        result.append(
            EntityCatalogueState(
                str(schema["entity_id"]),
                "SCHEMA",
                schema_status,
                (target,) if schema_status == "ACTIVE" else (),
                tuple(actual_by_entity.get(str(schema["entity_id"]), ())),
            )
        )
    return result


def meter_catalogues_state(
    *,
    service_entity_id: str,
    index_target: CatalogueTarget,
    rules: Iterable[Mapping[str, Any]],
    pages_by_path: Mapping[str, CatalogueTarget],
    actual_by_entity: Mapping[str, Iterable[str]],
) -> list[EntityCatalogueState]:
    result = [
        EntityCatalogueState(
            service_entity_id,
            "SERVICE",
            "ACTIVE",
            (index_target,),
            tuple(actual_by_entity.get(service_entity_id, ())),
        )
    ]
    for rule in sorted(rules, key=lambda item: str(item["entity_id"])):
        entity_id = str(rule["entity_id"])
        status = str(rule["status"])
        desired: tuple[CatalogueTarget, ...] = ()
        if status == "ACTIVE":
            path = str(rule["source_page_path"])
            desired = (pages_by_path.get(path, CatalogueTarget(None, path, None)),)
        result.append(
            EntityCatalogueState(
                entity_id,
                "MONITOR_RULE",
                status,
                desired,
                tuple(actual_by_entity.get(entity_id, ())),
            )
        )
    return result
