#!/usr/bin/env python3
"""Attended exact-set WORK reconciliation for one generated-artifact family.

This command is deliberately only a bridge.  Condition policy and briefing
construction remain in generated_conditions.py; WORK remains the sole writer.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
from typing import Any, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

from generated_conditions import (
    CONDITION_KINDS,
    CatalogueTarget,
    EntityCatalogueState,
    derive_generated_artifact_conditions,
    meter_catalogues_state,
    schema_catalogues_state,
    work_catalogues_state,
)


TOKEN_ENV = "DOCPLANE_GENERATED_CONDITIONS_TOKEN"
PRINCIPAL_ID_ENV = "DOCPLANE_GENERATED_CONDITIONS_PRINCIPAL_ID"


@dataclass(frozen=True)
class FamilySpec:
    artifact_key: str
    source_kind: str
    source_key: str
    index_path: str


FAMILIES = {
    "work": FamilySpec("work-catalogue", "SYSTEM", "docplane-work", "work/index.md"),
    "schema": FamilySpec(
        "schema-catalogue-docplane",
        "DATABASE",
        "docplane",
        "model/schema-catalogue/docplane/index.md",
    ),
    "meter": FamilySpec(
        "meter-list-hub2.prometheus",
        "SERVICE",
        "hub2.prometheus",
        "observe/meter-list/hub2-prometheus/index.md",
    ),
}


class RunnerError(RuntimeError):
    """Bounded operational failure that is safe to print."""


class Client:
    """Minimal client whose only mutation method is the WORK exact-set PUT."""

    def __init__(self, base_url: str, token: str, opener=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._open = opener or urllib.request.urlopen

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with self._open(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # Never echo an uncontrolled response body: it is outside this
            # command's bounded machine-output contract.
            raise RunnerError(f"DocPlane API request failed: HTTP {error.code}") from error
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise RunnerError("DocPlane API request failed or returned invalid JSON") from error

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def put_conditions(
        self, artifact_id: str, payload: Mapping[str, Any], idempotency_key: str
    ) -> Any:
        return self._request(
            "PUT",
            f"/api/v1/work/generated-artifacts/{artifact_id}/conditions",
            payload,
            idempotency_key,
        )


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RunnerError(f"required environment variable is absent: {name}")
    return value


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as error:
        raise RunnerError(f"{label} must be a UUID") from error


def _list(client: Client, path: str, key: str) -> list[dict[str, Any]]:
    value = client.get(path)
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        raise RunnerError(f"DocPlane read contract missing list: {key}")
    if value.get("truncated"):
        raise RunnerError(f"DocPlane read is truncated: {key}")
    return value[key]


def _entity(client: Client, entity_id: str) -> dict[str, Any]:
    value = client.get(f"/api/v1/model/entities/{_uuid(entity_id, 'entity ID')}")
    if not isinstance(value, dict) or value.get("entity_id") != entity_id:
        raise RunnerError("MODEL entity identity is missing or conflicting")
    return value


def _actual_catalogues(client: Client, entity_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for entity_id in sorted(set(entity_ids)):
        detail = _entity(client, entity_id)
        ids = [
            str(page["page_resource_id"])
            for page in detail.get("pages", [])
            if page.get("relation") == "CATALOGUES"
        ]
        if len(ids) != len(set(ids)):
            raise RunnerError("duplicate CATALOGUES relationship in MODEL read")
        result[entity_id] = tuple(sorted(ids))
    return result


def _page_target(
    client: Client, path: str, artifact_paths: set[str]
) -> CatalogueTarget:
    query = urllib.parse.urlencode({"path": path, "status": "all"})
    pages = _list(client, f"/api/v1/pages?{query}", "pages")
    if len(pages) > 1:
        raise RunnerError("generated target page path is ambiguous")
    if not pages:
        return CatalogueTarget(None, path, None)
    page = pages[0]
    resource_id = _uuid(str(page.get("resource_id")), "page resource ID")
    target_status = (
        "active"
        if path in artifact_paths
        and page.get("status") == "active"
        and page.get("provenance") == "GENERATED"
        else None
    )
    return CatalogueTarget(resource_id, path, target_status)


def _artifact_context(client: Client, spec: FamilySpec) -> tuple[dict[str, Any], dict[str, Any]]:
    artifacts = _list(client, "/api/v1/model/artifacts?status=DECLARED&limit=1000", "artifacts")
    matches = [item for item in artifacts if item.get("artifact_key") == spec.artifact_key]
    if len(matches) != 1:
        raise RunnerError(
            f"expected exactly one DECLARED artifact for family key {spec.artifact_key}; found {len(matches)}"
        )
    artifact = matches[0]
    if artifact.get("status") != "DECLARED":
        raise RunnerError("family artifact is not DECLARED")
    artifact_id = _uuid(str(artifact.get("artifact_id")), "artifact ID")
    source_id = _uuid(str(artifact.get("source_entity_id")), "source entity ID")
    source = _entity(client, source_id)
    if (source.get("entity_kind"), source.get("entity_key"), source.get("status")) != (
        spec.source_kind,
        spec.source_key,
        "ACTIVE",
    ):
        raise RunnerError("family artifact source identity does not match the reviewed mapping")
    status = client.get(f"/api/v1/model/artifacts/{artifact_id}/status")
    if not isinstance(status, dict) or (
        status.get("artifact_id"), status.get("artifact_key"), status.get("source_entity_id")
    ) != (artifact_id, spec.artifact_key, source_id):
        raise RunnerError("artifact status identity is missing or conflicting")
    status["artifact"] = artifact
    return status, source


def _work_state(
    client: Client, spec: FamilySpec, artifact: Mapping[str, Any], source: Mapping[str, Any]
):
    paths = set(artifact.get("target_page_paths") or [])
    target = _page_target(client, spec.index_path, paths)
    actual = _actual_catalogues(client, [str(source["entity_id"])])
    return work_catalogues_state(
        system_entity_id=str(source["entity_id"]),
        index_target=target,
        actual_page_resource_ids=actual[str(source["entity_id"])],
    )


def _schema_state(
    client: Client, spec: FamilySpec, artifact: Mapping[str, Any], source: Mapping[str, Any]
):
    entities = _list(
        client,
        "/api/v1/model/entities?entity_kind=SCHEMA&status=all&limit=1000",
        "entities",
    )
    prefix = f"{spec.source_key}."
    schemas = [item for item in entities if str(item.get("entity_key", "")).startswith(prefix)]
    paths = set(artifact.get("target_page_paths") or [])
    adapted: list[dict[str, Any]] = []
    paths_by_entity: dict[str, str] = {}
    for entity in schemas:
        key = str(entity["entity_key"])
        name = key.removeprefix(prefix)
        if not name or "/" in name or ".." in name:
            raise RunnerError("SCHEMA entity key cannot map to a canonical catalogue path")
        paths_by_entity[str(entity["entity_id"])] = (
            f"model/schema-catalogue/{spec.source_key}/{name}.md"
        )
    entity_ids = [str(source["entity_id"]), *(str(item["entity_id"]) for item in schemas)]
    actual = _actual_catalogues(client, entity_ids)
    current_schema_paths = paths - {spec.index_path}
    known_paths = set(paths_by_entity.values())
    if current_schema_paths - known_paths:
        raise RunnerError("generated SCHEMA target has no unique structured MODEL identity")
    stale: list[EntityCatalogueState] = []
    for entity in schemas:
        entity_id = str(entity["entity_id"])
        path = paths_by_entity[entity_id]
        if path in current_schema_paths:
            adapted.append(
                {
                    **entity,
                    "catalogue_target": _page_target(client, path, paths),
                }
            )
        else:
            # SCHEMA lifecycle and projection membership are separate.  Keep
            # the real entity status in evidence while making the absent
            # generated target's desired semantic set explicitly empty.
            stale.append(
                EntityCatalogueState(
                    entity_id,
                    "SCHEMA",
                    str(entity.get("status")),
                    (),
                    actual.get(entity_id, ()),
                )
            )
    current = schema_catalogues_state(
        database_entity_id=str(source["entity_id"]),
        index_target=_page_target(client, spec.index_path, paths),
        schemas=adapted,
        actual_by_entity=actual,
    )
    return [*current, *sorted(stale, key=lambda item: item.entity_id)]


def _meter_state(
    client: Client, spec: FamilySpec, artifact: Mapping[str, Any], source: Mapping[str, Any]
):
    rules = _list(
        client,
        "/api/v1/model/entities?entity_kind=MONITOR_RULE&status=all&limit=1000",
        "entities",
    )
    paths = set(artifact.get("target_page_paths") or [])
    source_paths: set[str] = set()
    for rule in rules:
        if rule.get("status") == "ACTIVE":
            path = (rule.get("attributes") or {}).get("source_page_path")
            if not isinstance(path, str) or not path:
                raise RunnerError("ACTIVE MONITOR_RULE lacks structured source_page_path")
            rule["source_page_path"] = path
            source_paths.add(path)
        else:
            rule["source_page_path"] = str((rule.get("attributes") or {}).get("source_page_path") or "")
    pages = {path: _page_target(client, path, paths) for path in sorted(source_paths)}
    entity_ids = [str(source["entity_id"]), *(str(item["entity_id"]) for item in rules)]
    actual = _actual_catalogues(client, entity_ids)
    return meter_catalogues_state(
        service_entity_id=str(source["entity_id"]),
        index_target=_page_target(client, spec.index_path, paths),
        rules=rules,
        pages_by_path=pages,
        actual_by_entity=actual,
    )


ADAPTERS = {"work": _work_state, "schema": _schema_state, "meter": _meter_state}


def _family_spec(family: str) -> FamilySpec:
    try:
        return FAMILIES[family]
    except KeyError as error:
        raise RunnerError("unknown generated-artifact family") from error


def build_request(client: Client, family: str) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = _family_spec(family)
    status, source = _artifact_context(client, spec)
    artifact = status["artifact"]
    catalogues = ADAPTERS[family](client, spec, artifact, source)
    derived = derive_generated_artifact_conditions(status, catalogues)
    conditions = [
        {"condition_kind": item["condition_kind"], "briefing": item["briefing"]}
        for item in derived
    ]
    kinds = [item["condition_kind"] for item in conditions]
    if kinds != sorted(set(kinds)) or any(kind not in CONDITION_KINDS for kind in kinds):
        raise RunnerError("condition derivation returned an invalid exact set")
    # Serialize before mutation so non-JSON adapter output fails closed.
    json.dumps({"conditions": conditions}, sort_keys=True)
    return status, {"conditions": conditions}


def _condition_kind_list(value: Mapping[str, Any], field: str) -> list[str]:
    collection = value.get(field)
    if not isinstance(collection, list):
        raise RunnerError(f"WORK reconciliation response has invalid {field}")
    if any(not isinstance(kind, str) or kind not in CONDITION_KINDS for kind in collection):
        raise RunnerError(f"WORK reconciliation response has invalid {field}")
    if len(collection) != len(set(collection)):
        raise RunnerError(f"WORK reconciliation response has duplicate {field}")
    return collection


def _bounded_receipt(
    value: Any,
    *,
    expected_artifact_id: str,
    desired_condition_kinds: list[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunnerError("WORK reconciliation response is not an object")
    artifact_id = value.get("artifact_id")
    if not isinstance(artifact_id, str):
        raise RunnerError("WORK reconciliation response has invalid artifact_id")
    try:
        artifact_id = _uuid(artifact_id, "WORK reconciliation artifact ID")
    except RunnerError as error:
        raise RunnerError("WORK reconciliation response has invalid artifact_id") from error
    if artifact_id != expected_artifact_id:
        raise RunnerError("WORK reconciliation receipt names a conflicting artifact")

    collections = {
        field: _condition_kind_list(value, field)
        for field in (
            "desired_condition_kinds",
            "opened",
            "reopened",
            "refreshed",
            "resolved",
            "continuing",
        )
    }
    if collections["desired_condition_kinds"] != desired_condition_kinds:
        raise RunnerError("WORK reconciliation response desired set does not match request")

    desired = set(desired_condition_kinds)
    desired_categories = (
        "opened",
        "reopened",
        "refreshed",
        "continuing",
    )
    category_sets = [set(collections[field]) for field in desired_categories]
    if sum(len(items) for items in category_sets) != len(set().union(*category_sets)):
        raise RunnerError("WORK reconciliation response desired-state categories overlap")
    if set().union(*category_sets) != desired:
        raise RunnerError("WORK reconciliation response does not partition the desired set")
    if set(collections["resolved"]) & desired:
        raise RunnerError("WORK reconciliation response resolves a desired condition")

    changed = value.get("changed")
    expected_changed = any(
        collections[field] for field in ("opened", "reopened", "refreshed", "resolved")
    )
    if not isinstance(changed, bool) or changed is not expected_changed:
        raise RunnerError("WORK reconciliation response has contradictory changed status")

    return {
        "artifact_id": artifact_id,
        **collections,
        "changed": changed,
    }


def reconcile(client: Client, family: str, idempotency_key: str, expected_principal_id: str) -> dict[str, Any]:
    _family_spec(family)
    invocation_id = _uuid(idempotency_key, "idempotency key")
    principal_id = _uuid(expected_principal_id, "expected principal ID")
    identity = client.get("/api/v1/me")
    if not isinstance(identity, dict) or (
        identity.get("principal_id"), identity.get("principal_kind")
    ) != (principal_id, "AUTOMATION"):
        raise RunnerError("authenticated identity does not match the dedicated AUTOMATION contract")
    status, payload = build_request(client, family)
    artifact = status["artifact"]
    response = client.put_conditions(str(artifact["artifact_id"]), payload, invocation_id)
    desired_condition_kinds = [item["condition_kind"] for item in payload["conditions"]]
    receipt = _bounded_receipt(
        response,
        expected_artifact_id=str(artifact["artifact_id"]),
        desired_condition_kinds=desired_condition_kinds,
    )
    return {
        "artifact_id": artifact["artifact_id"],
        "artifact_key": artifact["artifact_key"],
        "family": family,
        "idempotency_key": invocation_id,
        "derived_condition_kinds": desired_condition_kinds,
        "request_condition_count": len(payload["conditions"]),
        "reconciliation": receipt,
        "replay_status": response.get("replayed") if isinstance(response.get("replayed"), bool) else "not_exposed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True, choices=tuple(FAMILIES))
    parser.add_argument("--idempotency-key", required=True, help="caller-supplied UUID; reuse only for an exact retry")
    args = parser.parse_args(argv)
    try:
        client = Client(
            _required_environment("DOCPLANE_API"),
            _required_environment(TOKEN_ENV),
        )
        result = reconcile(
            client,
            args.family,
            args.idempotency_key,
            _required_environment(PRINCIPAL_ID_ENV),
        )
    except RunnerError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
