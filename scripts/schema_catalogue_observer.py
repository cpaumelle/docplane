#!/usr/bin/env python3
"""Observe the Schema catalogue source without invoking generation.

The only durable write this program can request is one entity-scoped
``FRESHNESS_CHECK`` through the DocPlane OBSERVE API. A fingerprint mismatch is
evidence for readers of that API; it never authorises generation or repair.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schema_catalogue_source import fingerprint, introspect  # noqa: E402,F401

SOURCE_ENTITY_KIND = "DATABASE"


class ObserveClient:
    """Minimal client: source identity read plus OBSERVE evidence write."""

    def __init__(self, base_url: str, token: str, opener=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._open = opener or urllib.request.urlopen

    def call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with self._open(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # Bound errors: never echo response bodies which may contain data
            # outside the observer's safe journal contract.
            raise RuntimeError(f"DocPlane API returned HTTP {error.code}") from error


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required; refusing to fall back to another principal")
    return value


def _probe_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("probe ID must be a UUID") from exc


def resolve_source_entity(client: ObserveClient, db_key: str) -> dict[str, Any]:
    """Resolve existing identity only; absence/ambiguity is never repaired."""
    listing = client.call("GET", "/api/v1/model/entities?entity_kind=DATABASE&limit=1000")
    matches = [
        entity for entity in listing.get("entities", [])
        if entity.get("entity_key") == db_key
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one DATABASE source entity; found {len(matches)}")
    return matches[0]


def record_observation(
    client: ObserveClient,
    *,
    source: dict[str, Any],
    probe_id: str,
    outcome: str,
    source_fingerprint: str | None = None,
    failure_stage: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    payload = {"probe": "schema-catalogue-source"}
    if failure_stage is not None:
        payload.update({"stage": failure_stage, "error_class": error_class})
    observation = {
        "subject_entity_id": source["entity_id"],
        "observation_kind": "FRESHNESS_CHECK",
        "outcome": outcome,
        "summary": (
            "Observed authoritative Schema source for schema-catalogue"
            if outcome == "NOMINAL"
            else f"Schema-catalogue source observation failed during {failure_stage}"
        ),
        "payload": payload,
        "idempotency_key": f"schema-catalogue:source-probe:{probe_id}:observation",
    }
    if source_fingerprint is not None:
        observation["source_fingerprint"] = source_fingerprint
    response = client.call(
        "POST",
        "/api/v1/observations",
        {"observations": [observation]},
        f"schema-catalogue:source-probe:{probe_id}:batch",
    )
    receipt = (response.get("recorded") or [{}])[0]
    return {
        "probe_id": probe_id,
        "source_entity": {
            "entity_id": source["entity_id"],
            "entity_kind": source.get("entity_kind", SOURCE_ENTITY_KIND),
            "entity_key": source.get("entity_key"),
        },
        "source_fingerprint": source_fingerprint,
        "observation_id": receipt.get("observation_id"),
        "outcome": outcome,
    }


def observe_source(
    client: ObserveClient,
    *,
    dsn: str,
    db_key: str,
    schemas: list[str],
    probe_id: str,
    connector=psycopg2.connect,
) -> tuple[dict[str, Any], bool]:
    """Read Schema and emit exactly one FRESHNESS_CHECK after identity resolves."""
    source = resolve_source_entity(client, db_key)
    try:
        with connector(dsn) as connection:
            structure = introspect(connection, schemas)
    except Exception as exc:
        return record_observation(
            client,
            source=source,
            probe_id=probe_id,
            outcome="FAILED",
            failure_stage="INTROSPECT_SOURCE",
            error_class=type(exc).__name__,
        ), False
    try:
        source_fingerprint = fingerprint(structure)
    except Exception as exc:
        return record_observation(
            client,
            source=source,
            probe_id=probe_id,
            outcome="FAILED",
            failure_stage="CANONICALIZE_SOURCE",
            error_class=type(exc).__name__,
        ), False
    return record_observation(
        client,
        source=source,
        probe_id=probe_id,
        outcome="NOMINAL",
        source_fingerprint=source_fingerprint,
    ), True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probe-id", type=_probe_id, help="UUID identity for an exact retry")
    args = parser.parse_args(argv)

    probe_id = args.probe_id or str(uuid4())
    try:
        client = ObserveClient(
            _required_environment("DOCPLANE_API"),
            _required_environment("DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
        )
        schemas = [
            item.strip()
            for item in _required_environment("CATALOGUE_SCHEMAS").split(",")
            if item.strip()
        ]
        result, succeeded = observe_source(
            client,
            dsn=_required_environment("CATALOGUE_SOURCE_DSN"),
            db_key=_required_environment("CATALOGUE_DB_KEY"),
            schemas=schemas,
            probe_id=probe_id,
        )
    except Exception as exc:
        # Identity and OBSERVE-write failures cannot safely create fallback
        # evidence. Journal only a bounded class, never uncontrolled text.
        print(
            json.dumps({"probe_id": probe_id, "outcome": "FAILED", "error_class": type(exc).__name__}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
