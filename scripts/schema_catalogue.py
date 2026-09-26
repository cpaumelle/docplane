#!/usr/bin/env python3
"""Schema catalogue generator — the Sprint 5 exemplar (DOMAIN_MODEL.md, A).

Reads STRUCTURE-ONLY metadata from a source PostgreSQL, passes every rendered
document through the canonical redaction transform (fail-closed), and drives
the DocPlane API as a named AUTOMATION principal:

  model    DATABASE + SCHEMA entities, STORES_IN wires
  know     one overview page per database and one page per schema (table
           sections, Mermaid ER diagrams, configured viewpoints) behind a
           permanent presence page, published atomically with exact GENERATED
           ownership membership through change -> validate -> publish
  model    exact DATABASE/SCHEMA CATALOGUES links after safe publication
  model    the structured schema projection (tables, columns, keys, indexes,
           relations, provenance) stored against the artifact — the surface
           agents query at table/column granularity (e004787b decision D1)
  observe  a GENERATION observation carrying the structural fingerprint, last

Regeneration is fingerprint-bound: when the source structure hash equals the
artifact's last GENERATION fingerprint, the render configuration is unchanged
and the stored projection corresponds, the run exits without mutating anything
(decision D2: scheduled generation produces no churn for an unchanged schema).
Idempotency keys are derived from the render identity, so a retried run
replays receipts instead of duplicating work.

Environment:
  DOCPLANE_API                     routed front, e.g. https://docplane.internal
  DOCPLANE_SCHEMA_CATALOGUE_TOKEN  AUTOMATION bearer (never logged)
  CATALOGUE_SOURCE_DSN             source database DSN (structure is read with
                                   a read-only transaction)
  CATALOGUE_DB_KEY                 entity key for the database, e.g. docplane
  CATALOGUE_DB_DISPLAY             display name, e.g. "DocPlane PostgreSQL"
  CATALOGUE_SCHEMAS                comma-separated schema names to catalogue
  CATALOGUE_ENVIRONMENT            provenance: environment label, e.g. production
  CATALOGUE_SOURCE_IDENTITY        provenance: non-secret source label, e.g.
                                   hub2/charliehub-postgres
  CATALOGUE_CONFIG                 optional presentation config; defaults to
                                   config/schema-catalogue/<db_key>.yml

Usage: schema_catalogue.py [--dry-run] [--emit-projection PATH] [--emit-pages DIR]
  --dry-run introspects, fingerprints, renders and redacts, then prints the
  plan without calling the DocPlane API. --emit-projection / --emit-pages write
  the redacted projection and pages locally for review (dry-run only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migration.redaction import redact  # noqa: E402

# The authoritative source projection (structure introspection + fingerprint)
# lives in a pure, side-effect-free module so the SCHEDULED schema observer
# imports the same seam without importing this mutation-capable generator.
# This is the sole implementation of introspect()/fingerprint(); re-exporting
# the names keeps `schema_catalogue.introspect` / `.fingerprint` working.
from schema_catalogue_source import fingerprint, introspect  # noqa: E402,F401
import schema_catalogue_projection as projection  # noqa: E402

GENERATOR_NAME = "docplane-schema-catalogue"
GENERATOR_VERSION = "2.0.0"
# Contract 2: enriched source structure (FK column pairs, CHECK, index columns,
# udt/enum/array, views; pg_catalog-only with a pinned search_path) plus the
# stored structured projection. A contract change is the one change that
# requires successor handoff of the artifact.
PROJECTION_CONTRACT_VERSION = 2
SECTION = "model/schema-catalogue"
CONFIG_DIR = ROOT / "config" / "schema-catalogue"

# Generated catalogue pages are REFERENCE: they describe what exists now.
# Without a marker every generated page lands in the observatory's
# missing_lifecycle signal, so each new rule file or schema silently enlarged
# a structural signal that nobody could act on (capture 075a36de).
# The lifecycle of the underlying *thing* lives in its own system; a
# projection's header must not try to carry it.
LIFECYCLE = "REFERENCE"
LIFECYCLE_LINES = (f"**Lifecycle:** {LIFECYCLE}", f"<!-- lifecycle: {LIFECYCLE} -->")

PRESENCE_PATH = f"{SECTION}/index.md"

GENERATOR = {
    "name": GENERATOR_NAME,
    "version": GENERATOR_VERSION,
    "projection_contract_version": PROJECTION_CONTRACT_VERSION,
}


class ProjectionRefusedError(RuntimeError):
    """The structured projection would not survive canonical redaction intact."""


# ── Rendering: deterministic markdown, redaction-gated at the boundary ──────

def render_pages(
    db_key: str,
    db_display: str,
    structure: dict[str, Any],
    structure_hash: str,
    config: dict[str, Any] | None = None,
    static_provenance: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Catalogue pages for one database. Deterministic for a given structure,
    configuration and static provenance.

    Every document is passed through the canonical redaction transform before
    it leaves this function — a refusal aborts the run rather than publishing
    partially redacted content.
    """
    pages = projection.render_pages(
        section=SECTION,
        lifecycle_lines=LIFECYCLE_LINES,
        db_key=db_key,
        db_display=db_display,
        structure=structure,
        structure_hash=structure_hash,
        config=config or {"schemas": {}},
        static_provenance=static_provenance or {},
        generator=GENERATOR,
    )
    for page in pages:
        # Fail-closed boundary: DocumentRefusedError from the canonical
        # transform aborts the run before anything leaves the source side.
        page["content"] = redact(page["content"], label="schema-catalogue").sanitised
    return pages


def guard_projection(document: dict[str, Any], provenance: dict[str, Any]) -> None:
    """The structured projection is published verbatim or not at all.

    Pages may be sanitised in place; a machine-readable projection may not,
    because a silently altered value would be a different structure from the
    one its fingerprint names. Any change the canonical transform would make
    refuses the run (DocumentRefusedError propagates the same way).
    """
    # Scanned per string leaf (mapping keys included), not as one serialised
    # blob: JSON punctuation adjacent to ordinary prose -- e.g. a column comment
    # ending "...are redacted." followed by `"}` -- otherwise forms a false
    # malformed-marker token. Every value an agent can read is still scanned.
    # The two SHA-256 digests the generator itself computes are shape-validated
    # instead: 64 hex characters is precisely what the HEX_SECRET rule exists
    # to catch, and these are identities, not credentials.
    digests = {key: document[key] for key in ("source_fingerprint", "config_hash")}
    for key, value in digests.items():
        if not (isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)):
            raise ProjectionRefusedError(f"{key} is not a SHA-256 hex digest")
    scanned = {key: value for key, value in document.items() if key not in digests}
    for label, value in (("projection", scanned), ("provenance", provenance)):
        for text in _string_leaves(value):
            result = redact(text, label=f"schema-catalogue-{label}")
            if result.changed:
                raise ProjectionRefusedError(
                    f"canonical redaction would alter the {label}; refusing to publish "
                    f"(classes: {sorted(set(result.findings))})"
                )


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


def render_identity(structure_hash: str, render_config_hash: str) -> str:
    """Identity of one rendered projection: structure plus presentation."""
    return hashlib.sha256(f"{structure_hash}:{render_config_hash}".encode("utf-8")).hexdigest()


def config_path(db_key: str) -> Path:
    override = os.environ.get("CATALOGUE_CONFIG", "").strip()
    return Path(override) if override else CONFIG_DIR / f"{db_key}.yml"


def source_metadata(conn) -> dict[str, Any]:
    """Volatile, non-structural source facts for provenance only.

    Never fingerprinted and never rendered into pages, so an engine patch
    release cannot cause publication churn.
    """
    cur = conn.cursor()
    cur.execute("SELECT current_database(), current_setting('server_version')")
    database_name, server_version = cur.fetchone()
    conn.rollback()
    return {"database_name": database_name, "server_version": server_version}


def presence_page() -> dict[str, str]:
    """The permanent hand-curated entry point. Created once if absent, never
    replaced by the generator, never listed as an artifact target."""
    return {
        "path": PRESENCE_PATH,
        "title": "Schema catalogue",
        "nav_path": "Model / Schema catalogue / Overview",
        "content": (
            "# Schema catalogue\n\n"
            f"**Lifecycle:** {LIFECYCLE}\n"
            f"<!-- lifecycle: {LIFECYCLE} -->\n\n"
            "Generated database schema documentation. Catalogue pages under "
            "this section carry `provenance=GENERATED`, are owned by the "
            f"`{GENERATOR_NAME}` AUTOMATION principal, and regenerate only "
            "when the source structural fingerprint changes.\n\n"
            "This presence page is permanent and hand-curated: it survives "
            "every regeneration and is the stable place for ownership notes "
            "and the regeneration runbook link.\n"
        ),
    }


# ── DocPlane API client: bearer auth, fingerprint-derived idempotency ───────

class ApiError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"DocPlane API {status}: {json.dumps(body, default=str)[:500]}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, base_url: str, token: str, opener=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._open = opener or urllib.request.urlopen

    def call(self, method: str, path: str, payload: Any = None, idempotency_key: str | None = None) -> Any:
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
            body = error.read().decode("utf-8", "replace")
            try:
                body = json.loads(body)
            except ValueError:
                pass
            raise ApiError(error.code, body) from error


def _key(identity: str, verb: str, discriminator: str = "") -> str:
    """Render-identity-bound AND generator-versioned: a fixed generator must
    never replay receipts persisted by a buggy predecessor for the same
    structure (the stale-DRAFT-change lesson from the first canary run), and a
    presentation-only change must not replay the previous render's receipts."""
    return (
        f"schema-catalogue-{GENERATOR_VERSION}-{identity[:16]}-{verb}"
        f"{'-' + discriminator if discriminator else ''}"
    )[:256]


# ── The run ─────────────────────────────────────────────────────────────────

def ensure_entities(
    client: Client,
    db_key: str,
    db_display: str,
    schemas: list[str],
    identity: str,
) -> dict[str, Any]:
    """Reconcile source MODEL identity and return structured catalogue mapping state.

    SCHEMA lifecycle is outside this projection slice, but schemas absent from
    the current source still need their active CATALOGUES assertion removed.
    Returning both current and absent entity IDs lets the semantic stage do so
    without inferring mappings from rendered Markdown.
    """
    existing = {
        entity["entity_key"]: entity
        for entity in client.call("GET", "/api/v1/model/entities?entity_kind=DATABASE").get("entities", [])
    }
    if db_key in existing:
        database_id = existing[db_key]["entity_id"]
    else:
        database_id = client.call(
            "POST", "/api/v1/model/entities",
            {"entity_kind": "DATABASE", "entity_key": db_key, "display_name": db_display},
            _key(identity, "entity", db_key),
        )["entity_id"]
    schema_entities = {
        entity["entity_key"]: entity
        for entity in client.call(
            "GET", "/api/v1/model/entities?entity_kind=SCHEMA&status=all&limit=1000"
        ).get("entities", [])
    }
    schema_ids: dict[str, str] = {}
    for schema in sorted(schemas):
        schema_key = f"{db_key}.{schema}"
        if schema_key in schema_entities:
            schema_id = schema_entities[schema_key]["entity_id"]
        else:
            schema_id = client.call(
                "POST", "/api/v1/model/entities",
                {"entity_kind": "SCHEMA", "entity_key": schema_key, "display_name": f"{db_display} {schema}"},
                _key(identity, "entity", schema_key),
            )["entity_id"]
        schema_ids[schema] = schema_id
        # Always wire the link, even for a pre-existing entity: a resumed run
        # may have created the entity without reaching this call, and the
        # server inserts links ON CONFLICT DO NOTHING, so replays are safe.
        client.call(
            "POST", f"/api/v1/model/entities/{schema_id}/links",
            {"relation": "STORES_IN", "to_entity_id": database_id},
            _key(identity, "link", schema_key),
        )
    prefix = f"{db_key}."
    stale_schema_ids = sorted(
        entity["entity_id"]
        for key, entity in schema_entities.items()
        if key.startswith(prefix) and key.removeprefix(prefix) not in schema_ids
    )
    return {
        "database_id": database_id,
        "schema_ids": schema_ids,
        "stale_schema_ids": stale_schema_ids,
    }


def current_catalogues_page_ids(client: Client, entity_id: str) -> list[str]:
    """Read only the active semantic catalogue set; other relations are opaque."""
    detail = client.call("GET", f"/api/v1/model/entities/{entity_id}")
    return sorted(
        page["page_resource_id"]
        for page in detail.get("pages", [])
        if page.get("relation") == "CATALOGUES"
    )


def reconcile_catalogues(
    client: Client,
    desired_by_entity: dict[str, list[str]],
    *,
    key_prefix: str,
) -> list[dict[str, Any]]:
    """Converge exact MODEL -> KNOW semantics without conferring ownership.

    Reads precede writes so an already exact projection is genuinely
    zero-mutation. A fresh request identity is correct here: after an unknown
    HTTP outcome the next invocation reads committed state first, while an
    uncommitted transaction can safely receive a new exact-set request.
    """
    results = []
    for entity_id, desired_ids in sorted(desired_by_entity.items()):
        desired = sorted(desired_ids)
        current = current_catalogues_page_ids(client, entity_id)
        if current == desired:
            continue
        results.append(
            client.call(
                "PUT",
                f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
                {"page_resource_ids": desired},
                f"{key_prefix}-catalogues-{uuid4()}",
            )
        )
    return results


def page_ids_for_paths(client: Client, paths: list[str]) -> dict[str, str]:
    """Resolve exact active-or-archived page identity without path inference."""
    resolved: dict[str, str] = {}
    for path in paths:
        pages = client.call("GET", f"/api/v1/pages?path={path}&status=all").get("pages", [])
        if not pages:
            raise RuntimeError(f"generated catalogue target is missing: {path}")
        resolved[path] = pages[0]["resource_id"]
    return resolved


def schema_catalogues_mappings(
    entities: dict[str, Any],
    page_ids: dict[str, str],
    db_key: str,
) -> dict[str, list[str]]:
    """Derive DATABASE/SCHEMA mappings from introspection identity, never content."""
    desired = {
        entities["database_id"]: [page_ids[f"{SECTION}/{db_key}/index.md"]],
    }
    for schema, entity_id in sorted(entities["schema_ids"].items()):
        desired[entity_id] = [page_ids[f"{SECTION}/{db_key}/{schema}.md"]]
    for entity_id in entities["stale_schema_ids"]:
        desired[entity_id] = []
    return desired


def write_projection_metrics(path: str, *, artifact: str, drift: bool, success: bool) -> None:
    """Atomically publish the generated-projection status metrics for node_exporter.

    ONE writer for every generated projection (work catalogue, meter list, ...), so a new
    projection inherits the same three series and the alerts that already read them rather
    than growing a private metric name nobody alerts on. `artifact` is the label that keeps
    them apart.

    Atomic by rename: the textfile collector scrapes this directory continuously, and a
    partially written file is a parse error at scrape time — which reads as absence.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    content = (
        "# HELP docplane_generated_projection_drift Whether live source state differs from the published generation fingerprint.\n"
        "# TYPE docplane_generated_projection_drift gauge\n"
        f'docplane_generated_projection_drift{{artifact="{artifact}"}} {int(drift)}\n'
        "# HELP docplane_generated_projection_reconcile_success Whether the most recent reconciliation completed successfully.\n"
        "# TYPE docplane_generated_projection_reconcile_success gauge\n"
        f'docplane_generated_projection_reconcile_success{{artifact="{artifact}"}} {int(success)}\n'
        "# HELP docplane_generated_projection_last_run_unixtime Unix time of the most recent reconciliation status check.\n"
        "# TYPE docplane_generated_projection_last_run_unixtime gauge\n"
        f'docplane_generated_projection_last_run_unixtime{{artifact="{artifact}"}} {now}\n'
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(0o644)
    temporary.replace(destination)


def current_artifact(client: Client, artifact_key: str) -> dict[str, Any] | None:
    for artifact in client.call("GET", "/api/v1/model/artifacts").get("artifacts", []):
        if artifact["artifact_key"] == artifact_key and artifact.get("status") != "RETIRED":
            return artifact
    return None


def last_generation_fingerprint(client: Client, artifact_id: str) -> str | None:
    status = client.call("GET", f"/api/v1/model/artifacts/{artifact_id}/status")
    for row in status.get("current_status") or []:
        if row.get("observation_kind") == "GENERATION":
            return row.get("source_fingerprint")
    return None


def last_generation_observation_id(client: Client, artifact_id: str) -> str | None:
    status = client.call("GET", f"/api/v1/model/artifacts/{artifact_id}/status")
    for row in status.get("current_status") or []:
        if row.get("observation_kind") == "GENERATION":
            return row.get("observation_id")
    return None


def transition_identity(
    artifact: dict[str, Any] | None, base_generation_id: str | None, identity: str
) -> str:
    """Idempotency identity of one publication *transition*.

    Keys bound only to the target render identity are reused whenever a
    structure returns to an earlier shape (A -> B -> A: a migration rollback),
    and DocPlane then correctly refuses the different request data as
    IDEMPOTENCY_KEY_REUSED -- permanently wedging a scheduled generator.
    Binding to the artifact and its last GENERATION evidence (the "from"
    state) keeps a retried, partially completed transition replay-safe
    (the base is unchanged until GENERATION is emitted last) while every new
    transition gets fresh keys.
    """
    base = f"{(artifact or {}).get('artifact_id', 'none')}:{base_generation_id or 'genesis'}"
    return hashlib.sha256(f"{base}->{identity}".encode("utf-8")).hexdigest()


def needs_succession(artifact: dict[str, Any]) -> bool:
    """Only projection-contract identity, never source membership/build version."""
    return (
        artifact.get("projection_contract_version", 1)
        != PROJECTION_CONTRACT_VERSION
    )


def needs_reconciliation(
    artifact: dict[str, Any], desired_paths: list[str]
) -> bool:
    return (
        needs_succession(artifact)
        or sorted(artifact.get("target_page_paths") or []) != desired_paths
        or artifact.get("generator_version") != GENERATOR_VERSION
    )


def publish_pages(
    client: Client,
    pages: list[dict[str, str]],
    identity: str,
    include_presence: bool,
    artifact: dict[str, Any] | None,
    artifact_key: str,
    database_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Publish the exact page set and GENERATED ownership in one transaction."""
    # The pages listing filters by EXACT path, so resolve each wanted path
    # individually rather than assuming a prefix listing exists.
    def lookup(path: str) -> dict[str, Any] | None:
        found = client.call("GET", f"/api/v1/pages?path={path}&status=all").get("pages", [])
        return found[0] if found else None

    desired_paths = sorted(page["path"] for page in pages)
    existing: dict[str, dict[str, Any]] = {}
    page_ids: dict[str, str] = {}
    operations = []
    for page in pages:
        current = lookup(page["path"])
        if current is not None:
            existing[page["path"]] = current
            page_ids[page["path"]] = current["resource_id"]
            if current.get("status") == "archived":
                operations.append(
                    ("RESTORE_PAGE", current["resource_id"], current["revision"], page)
                )
            operations.append(
                ("REPLACE_DOCUMENT", current["resource_id"], current["revision"], page)
            )
        else:
            resource_id = str(uuid4())
            page_ids[page["path"]] = resource_id
            operations.append(
                ("CREATE_PAGE", None, None, {**page, "resource_id": resource_id})
            )

    if include_presence and lookup(PRESENCE_PATH) is None:
        operations.append(("CREATE_PAGE", None, None, presence_page()))

    stale_paths = sorted(
        set((artifact or {}).get("target_page_paths") or []) - set(desired_paths)
    )
    for path in stale_paths:
        current = lookup(path)
        if current is not None and current.get("status") != "archived":
            operations.append(
                ("ARCHIVE_PAGE", current["resource_id"], current["revision"], {"path": path})
            )

    if artifact is None:
        # Establish the artifact identity without targets. CREATE_PAGE UUIDs
        # are adopted inside publication, so no page commits as AUTHORED.
        artifact = client.call(
            "POST",
            "/api/v1/model/artifacts",
            {
                "artifact_key": artifact_key,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "projection_contract_version": PROJECTION_CONTRACT_VERSION,
                "source_entity_id": database_id,
                "redaction_policy": "canonical",
                "target_page_resource_ids": [],
                "target_page_paths": [],
            },
            _key(identity, "artifact-empty"),
        )

    target_ids = [page_ids[path] for path in desired_paths]
    if needs_succession(artifact):
        ownership_plan = {
            "mode": "SUCCESSOR",
            "predecessor_id": artifact["artifact_id"],
            "expected_version": artifact["version"],
            "target_page_resource_ids": target_ids,
            "target_page_paths": desired_paths,
            "generator_version": GENERATOR_VERSION,
            "successor": {
                "artifact_key": artifact_key,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "projection_contract_version": PROJECTION_CONTRACT_VERSION,
                "config_hash": artifact.get("config_hash"),
                "source_entity_id": artifact["source_entity_id"],
                "redaction_policy": artifact.get("redaction_policy", "canonical"),
                "target_page_resource_ids": target_ids,
                "target_page_paths": desired_paths,
            },
        }
    else:
        ownership_plan = {
            "mode": "IN_PLACE",
            "artifact_id": artifact["artifact_id"],
            "expected_version": artifact["version"],
            "target_page_resource_ids": target_ids,
            "target_page_paths": desired_paths,
            "generator_version": GENERATOR_VERSION,
        }

    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Schema catalogue regeneration {identity[:16]}",
            "purpose": (
                "Fingerprint-bound regeneration by the schema-catalogue "
                f"generator; render identity {identity} (source structure "
                "fingerprint plus presentation configuration)."
            ),
            "workspace_key": "reference",
            "generated_ownership_plan": ownership_plan,
        },
        _key(identity, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        request: dict[str, Any] = {"operation_type": operation_type, "payload": {}}
        if operation_type in {"CREATE_PAGE", "REPLACE_DOCUMENT"}:
            request["payload"] = {
                "path": page["path"],
                "title": page["title"],
                "nav_path": page["nav_path"],
                "content": page["content"],
            }
            # Generated pages carry a pre-assigned identity that publication
            # adopts into GENERATED custody; the authored presence page does
            # not, and must not be given one (it is never an artifact target).
            if operation_type == "CREATE_PAGE" and "resource_id" in page:
                request["payload"]["resource_id"] = page["resource_id"]
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call(
            "POST", f"/api/v1/changes/{change_id}/operations", request,
            _key(identity, "operation", f"{operation_type}:{page['path']}"),
        )
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(identity, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(identity, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")
    active = current_artifact(client, artifact_key)
    if active is None:
        raise RuntimeError("publication committed without an active generated-artifact owner")
    return active, page_ids


def emit_generation(
    client: Client, artifact_id: str, structure_hash: str, identity: str, summary: str
) -> None:
    """GENERATION evidence, last: the source fingerprint it consumed, keyed by
    the render identity so a presentation-only regeneration is recorded too."""
    client.call(
        "POST", "/api/v1/observations",
        {
            "observations": [
                {
                    "subject_artifact_id": artifact_id,
                    "observation_kind": "GENERATION",
                    "outcome": "NOMINAL",
                    "source_fingerprint": structure_hash,
                    "summary": summary,
                    "idempotency_key": _key(identity, "generation"),
                }
            ]
        },
        _key(identity, "observation-batch"),
    )


def current_projection(client: Client, artifact_id: str) -> dict[str, Any] | None:
    try:
        return client.call("GET", f"/api/v1/model/artifacts/{artifact_id}/projection")
    except ApiError as error:
        if error.status == 404:
            return None
        raise


def projection_current(
    stored: dict[str, Any] | None, structure_hash: str, render_config_hash: str
) -> bool:
    return (
        stored is not None
        and stored.get("source_fingerprint") == structure_hash
        and stored.get("config_hash") == render_config_hash
        and stored.get("projection_contract_version") == PROJECTION_CONTRACT_VERSION
    )


def publish_projection(
    client: Client,
    artifact_id: str,
    document: dict[str, Any],
    provenance: dict[str, Any],
    identity: str,
) -> dict[str, Any]:
    """Store the structured projection; DocPlane re-derives its fingerprint."""
    return client.call(
        "PUT",
        f"/api/v1/model/artifacts/{artifact_id}/projection",
        {
            "projection_kind": projection.PROJECTION_KIND,
            "projection_contract_version": PROJECTION_CONTRACT_VERSION,
            "source_fingerprint": document["source_fingerprint"],
            "config_hash": document["config_hash"],
            "document": document,
            "provenance": provenance,
        },
        _key(identity, "projection"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--emit-projection", type=Path)
    parser.add_argument("--emit-pages", type=Path)
    args = parser.parse_args(argv)
    if (args.emit_projection or args.emit_pages) and not args.dry_run:
        parser.error("--emit-projection/--emit-pages are review aids and require --dry-run")

    dsn = os.environ["CATALOGUE_SOURCE_DSN"]
    db_key = os.environ["CATALOGUE_DB_KEY"]
    db_display = os.environ.get("CATALOGUE_DB_DISPLAY", db_key)
    schemas = [name.strip() for name in os.environ["CATALOGUE_SCHEMAS"].split(",") if name.strip()]
    static_provenance = {
        "environment": os.environ.get("CATALOGUE_ENVIRONMENT", "unspecified").strip() or "unspecified",
        "source_identity": os.environ.get("CATALOGUE_SOURCE_IDENTITY", "unspecified").strip() or "unspecified",
    }
    config = projection.load_config(config_path(db_key))

    with psycopg2.connect(dsn) as source:
        structure = introspect(source, schemas)
    with psycopg2.connect(dsn) as source:
        metadata = source_metadata(source)
    structure_hash = fingerprint(structure)
    render_config_hash = projection.config_hash(config, static_provenance)
    identity = render_identity(structure_hash, render_config_hash)
    pages = render_pages(db_key, db_display, structure, structure_hash, config, static_provenance)
    document = projection.build_document(
        db_key, db_display, structure, structure_hash, render_config_hash,
        PROJECTION_CONTRACT_VERSION,
    )
    provenance = projection.build_provenance(
        config=config,
        structure=structure,
        static_provenance=static_provenance,
        source_metadata=metadata,
        extracted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        generator=GENERATOR,
    )
    guard_projection(document, provenance)
    print(f"fingerprint {structure_hash}")
    print(f"config {render_config_hash[:16]} render identity {identity[:16]}")
    print(f"rendered {len(pages)} catalogue pages for {len(schemas)} schemas")

    if args.dry_run:
        for page in pages:
            print(f"DRY-RUN would publish {page['path']} ({len(page['content'])} bytes)")
        size = len(json.dumps(document, sort_keys=True))
        print(
            f"DRY-RUN would store projection: {sum(len(s['tables']) for s in structure.values())} tables, "
            f"{len(document['relations'])} relations, {size} bytes"
        )
        if args.emit_projection:
            args.emit_projection.write_text(
                json.dumps({"document": document, "provenance": provenance}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        if args.emit_pages:
            for page in pages:
                target = args.emit_pages / page["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(page["content"], encoding="utf-8")
        return 0

    client = Client(os.environ["DOCPLANE_API"], os.environ["DOCPLANE_SCHEMA_CATALOGUE_TOKEN"])
    entities = ensure_entities(client, db_key, db_display, schemas, identity)
    artifact_key = f"schema-catalogue-{db_key}"
    artifact = current_artifact(client, artifact_key)
    desired_paths = sorted(page["path"] for page in pages)
    base_generation = (
        last_generation_observation_id(client, artifact["artifact_id"]) if artifact else None
    )
    transition = transition_identity(artifact, base_generation, identity)
    if artifact is not None:
        previous = last_generation_fingerprint(client, artifact["artifact_id"])
        stored = current_projection(client, artifact["artifact_id"])
        if (
            previous == structure_hash
            and not needs_reconciliation(artifact, desired_paths)
            and stored is not None
            and stored.get("config_hash") == render_config_hash
        ):
            page_ids = page_ids_for_paths(client, desired_paths)
            reconcile_catalogues(
                client,
                schema_catalogues_mappings(entities, page_ids, db_key),
                key_prefix=_key(transition, "semantic"),
            )
            if not projection_current(stored, structure_hash, render_config_hash):
                publish_projection(client, artifact["artifact_id"], document, provenance, transition)
                print(f"UNCHANGED {structure_hash[:16]} — repaired the structured projection only")
                return 0
            # `previous == structure_hash` proves that this projection already
            # has successful GENERATION evidence. Semantic maintenance is
            # durably evidenced by its MODEL receipt/event and must not reuse
            # the fingerprint-bound observation identity with a different body.
            print(f"UNCHANGED {structure_hash[:16]} — nothing to regenerate")
            return 0

        if (
            previous == structure_hash
            and sorted(artifact.get("target_page_paths") or []) == desired_paths
            and not needs_succession(artifact)
            and artifact.get("generator_version") != GENERATOR_VERSION
            and projection_current(stored, structure_hash, render_config_hash)
        ):
            page_ids = {
                page["path"]: client.call(
                    "GET", f"/api/v1/pages?path={page['path']}&status=all"
                )["pages"][0]["resource_id"]
                for page in pages
            }
            updated = client.call(
                "PUT",
                f"/api/v1/model/artifacts/{artifact['artifact_id']}/targets",
                {
                    "expected_version": artifact["version"],
                    "target_page_resource_ids": [page_ids[path] for path in desired_paths],
                    "target_page_paths": desired_paths,
                    "generator_version": GENERATOR_VERSION,
                },
                _key(transition, "artifact-attribution"),
            )
            artifact = updated["artifact"]
            reconcile_catalogues(
                client,
                schema_catalogues_mappings(entities, page_ids, db_key),
                key_prefix=_key(transition, "semantic"),
            )
            # Attribution and semantic receipts are the maintenance evidence;
            # the source fingerprint already has a successful generation.
            print(f"UNCHANGED {structure_hash[:16]} — updated generator attribution only")
            return 0

    artifact, page_ids = publish_pages(
        client,
        pages,
        transition,
        include_presence=True,
        artifact=artifact,
        artifact_key=artifact_key,
        database_id=entities["database_id"],
    )
    reconcile_catalogues(
        client,
        schema_catalogues_mappings(entities, page_ids, db_key),
        key_prefix=_key(transition, "semantic"),
    )
    publish_projection(client, artifact["artifact_id"], document, provenance, transition)
    emit_generation(
        client, artifact["artifact_id"], structure_hash, transition,
        f"Regenerated {len(pages)} catalogue pages and the structured projection for "
        f"{db_key} ({len(schemas)} schemas)",
    )
    print(
        f"PUBLISHED {len(page_ids)} pages and the structured projection, artifact "
        f"{artifact['artifact_id']}, fingerprint {structure_hash[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
