#!/usr/bin/env python3
"""Schema catalogue generator — the Sprint 5 exemplar (DOMAIN_MODEL.md, A).

Reads STRUCTURE-ONLY metadata from a source PostgreSQL, passes every rendered
document through the canonical redaction transform (fail-closed), and drives
the DocPlane API as a named AUTOMATION principal:

  model    DATABASE + SCHEMA entities, STORES_IN wires
  know     catalogue pages behind a permanent presence page,
           published atomically with exact GENERATED ownership membership
           through change -> validate -> publish
  model    exact DATABASE/SCHEMA CATALOGUES links after safe publication
  observe  a GENERATION observation carrying the structural fingerprint

Regeneration is fingerprint-bound: when the source structure hash equals the
artifact's last GENERATION fingerprint the run exits without mutating
anything. Idempotency keys are derived from the fingerprint, so a retried
run replays receipts instead of duplicating work.

Environment:
  DOCPLANE_API                     routed front, e.g. https://docplane.internal
  DOCPLANE_SCHEMA_CATALOGUE_TOKEN  AUTOMATION bearer (never logged)
  CATALOGUE_SOURCE_DSN             source database DSN (structure is read with
                                   a read-only transaction)
  CATALOGUE_DB_KEY                 entity key for the database, e.g. docplane
  CATALOGUE_DB_DISPLAY             display name, e.g. "DocPlane PostgreSQL"
  CATALOGUE_SCHEMAS                comma-separated schema names to catalogue

Usage: schema_catalogue.py [--dry-run]
  --dry-run introspects, fingerprints, renders and redacts, then prints the
  plan without calling the DocPlane API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from migration.redaction import redact  # noqa: E402

GENERATOR_NAME = "docplane-schema-catalogue"
GENERATOR_VERSION = "1.0.4"
PROJECTION_CONTRACT_VERSION = 1
SECTION = "model/schema-catalogue"
PRESENCE_PATH = f"{SECTION}/index.md"


# ── Introspection: structure only, deterministic order, never row data ──────

_TABLES_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
 ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = %s AND table_name = %s
 ORDER BY ordinal_position
"""

_CONSTRAINTS_SQL = """
SELECT con.contype, con.conname, pg_get_constraintdef(con.oid)
  FROM pg_constraint con
  JOIN pg_class c ON c.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relname = %s AND con.contype IN ('p', 'f', 'u')
 ORDER BY con.conname
"""

_INDEXES_SQL = """
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = %s AND tablename = %s
 ORDER BY indexname
"""


def introspect(conn, schemas: list[str]) -> dict[str, Any]:
    structure: dict[str, Any] = {}
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    for schema in sorted(schemas):
        tables = {}
        cur.execute(_TABLES_SQL, (schema,))
        for table_name, comment in cur.fetchall():
            cur.execute(_COLUMNS_SQL, (schema, table_name))
            columns = [
                {
                    "name": name,
                    "type": data_type,
                    "nullable": nullable == "YES",
                    "default": default,
                }
                for name, data_type, nullable, default in cur.fetchall()
            ]
            cur.execute(_CONSTRAINTS_SQL, (schema, table_name))
            constraints = [
                {"kind": kind, "name": name, "definition": definition}
                for kind, name, definition in cur.fetchall()
            ]
            cur.execute(_INDEXES_SQL, (schema, table_name))
            indexes = [
                {"name": name, "definition": definition}
                for name, definition in cur.fetchall()
            ]
            tables[table_name] = {
                "comment": comment,
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
            }
        structure[schema] = tables
    return structure


def fingerprint(structure: dict[str, Any]) -> str:
    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Rendering: deterministic markdown, redaction-gated at the boundary ──────

def _table_section(name: str, table: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}`", ""]
    if table["comment"]:
        lines += [table["comment"], ""]
    lines += ["| Column | Type | Nullable | Default |", "| --- | --- | --- | --- |"]
    for column in table["columns"]:
        default = f"`{column['default']}`" if column["default"] else ""
        lines.append(
            f"| `{column['name']}` | {column['type']} | "
            f"{'yes' if column['nullable'] else 'no'} | {default} |"
        )
    lines.append("")
    if table["constraints"]:
        lines.append("Constraints:")
        lines += [
            f"- `{item['name']}` — `{item['definition']}`"
            for item in table["constraints"]
        ]
        lines.append("")
    if table["indexes"]:
        lines.append("Indexes:")
        lines += [f"- `{item['name']}`" for item in table["indexes"]]
        lines.append("")
    return lines


def render_pages(
    db_key: str,
    db_display: str,
    structure: dict[str, Any],
    structure_hash: str,
) -> list[dict[str, str]]:
    """Catalogue pages for one database. Deterministic for a given structure.

    Every document is passed through the canonical redaction transform before
    it leaves this function — a refusal aborts the run rather than publishing
    partially redacted content.
    """
    stamp = (
        f"> Generated by `{GENERATOR_NAME}` {GENERATOR_VERSION} · "
        f"source fingerprint `{structure_hash[:16]}` · structure only, no row data. "
        "Edit through the generator, never by hand."
    )
    pages: list[dict[str, str]] = []
    schema_lines = []
    for schema in sorted(structure):
        tables = structure[schema]
        # Sibling link, not `{db_key}/…`: this index is emitted at
        # SECTION/<db_key>/index.md and the schema pages are its siblings in the
        # same directory. Prefixing the db key again resolves into a nonexistent
        # SECTION/<db_key>/<db_key>/<schema> path.
        schema_lines.append(
            f"- [`{schema}`]({schema}.md) — {len(tables)} tables"
        )
        body = [f"# {db_display} — `{schema}`", "", stamp, ""]
        for table_name in sorted(tables):
            body += _table_section(table_name, tables[table_name])
        pages.append(
            {
                "path": f"{SECTION}/{db_key}/{schema}.md",
                "title": f"{db_display} — {schema}",
                "nav_path": f"Model / Schema catalogue / {db_display} / {schema}",
                "content": "\n".join(body).rstrip() + "\n",
            }
        )
    overview = [
        f"# {db_display} schema catalogue",
        "",
        stamp,
        "",
        f"{len(structure)} schemas catalogued:",
        "",
        *schema_lines,
        "",
    ]
    # The corpus nav model is strict: a node is a page OR a section, never
    # both. The database node is a section (it parents the schema pages), so
    # its landing page takes the corpus's explicit "Overview" leaf idiom.
    pages.insert(
        0,
        {
            "path": f"{SECTION}/{db_key}/index.md",
            "title": f"{db_display} schema catalogue",
            "nav_path": f"Model / Schema catalogue / {db_display} / Overview",
            "content": "\n".join(overview),
        },
    )
    for page in pages:
        # Fail-closed boundary: DocumentRefusedError from the canonical
        # transform aborts the run before anything leaves the source side.
        page["content"] = redact(page["content"], label="schema-catalogue").sanitised
    return pages


def presence_page() -> dict[str, str]:
    """The permanent hand-curated entry point. Created once if absent, never
    replaced by the generator, never listed as an artifact target."""
    return {
        "path": PRESENCE_PATH,
        "title": "Schema catalogue",
        "nav_path": "Model / Schema catalogue / Overview",
        "content": (
            "# Schema catalogue\n\n"
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


def _key(structure_hash: str, verb: str, discriminator: str = "") -> str:
    """Fingerprint-bound AND generator-versioned: a fixed generator must never
    replay receipts persisted by a buggy predecessor for the same structure
    (the stale-DRAFT-change lesson from the first canary run)."""
    return (
        f"schema-catalogue-{GENERATOR_VERSION}-{structure_hash[:16]}-{verb}"
        f"{'-' + discriminator if discriminator else ''}"
    )[:256]


# ── The run ─────────────────────────────────────────────────────────────────

def ensure_entities(
    client: Client,
    db_key: str,
    db_display: str,
    schemas: list[str],
    structure_hash: str,
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
            _key(structure_hash, "entity", db_key),
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
                _key(structure_hash, "entity", schema_key),
            )["entity_id"]
        schema_ids[schema] = schema_id
        # Always wire the link, even for a pre-existing entity: a resumed run
        # may have created the entity without reaching this call, and the
        # server inserts links ON CONFLICT DO NOTHING, so replays are safe.
        client.call(
            "POST", f"/api/v1/model/entities/{schema_id}/links",
            {"relation": "STORES_IN", "to_entity_id": database_id},
            _key(structure_hash, "link", schema_key),
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
    structure_hash: str,
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
            _key(structure_hash, "artifact-empty"),
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
            "title": f"Schema catalogue regeneration {structure_hash[:16]}",
            "purpose": (
                "Fingerprint-bound regeneration by the schema-catalogue "
                f"generator; source structural fingerprint {structure_hash}."
            ),
            "workspace_key": "reference",
            "generated_ownership_plan": ownership_plan,
        },
        _key(structure_hash, "change"),
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
            if operation_type == "CREATE_PAGE":
                request["payload"]["resource_id"] = page["resource_id"]
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call(
            "POST", f"/api/v1/changes/{change_id}/operations", request,
            _key(structure_hash, "operation", f"{operation_type}:{page['path']}"),
        )
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(structure_hash, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(structure_hash, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")
    active = current_artifact(client, artifact_key)
    if active is None:
        raise RuntimeError("publication committed without an active generated-artifact owner")
    return active, page_ids


def emit_generation(client: Client, artifact_id: str, structure_hash: str, summary: str) -> None:
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
                    "idempotency_key": _key(structure_hash, "generation"),
                }
            ]
        },
        _key(structure_hash, "observation-batch"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    dsn = os.environ["CATALOGUE_SOURCE_DSN"]
    db_key = os.environ["CATALOGUE_DB_KEY"]
    db_display = os.environ.get("CATALOGUE_DB_DISPLAY", db_key)
    schemas = [name.strip() for name in os.environ["CATALOGUE_SCHEMAS"].split(",") if name.strip()]

    with psycopg2.connect(dsn) as source:
        structure = introspect(source, schemas)
    structure_hash = fingerprint(structure)
    pages = render_pages(db_key, db_display, structure, structure_hash)
    print(f"fingerprint {structure_hash}")
    print(f"rendered {len(pages)} catalogue pages for {len(schemas)} schemas")

    if args.dry_run:
        for page in pages:
            print(f"DRY-RUN would publish {page['path']} ({len(page['content'])} bytes)")
        return 0

    client = Client(os.environ["DOCPLANE_API"], os.environ["DOCPLANE_SCHEMA_CATALOGUE_TOKEN"])
    entities = ensure_entities(client, db_key, db_display, schemas, structure_hash)
    artifact_key = f"schema-catalogue-{db_key}"
    artifact = current_artifact(client, artifact_key)
    desired_paths = sorted(page["path"] for page in pages)
    if artifact is not None:
        previous = last_generation_fingerprint(client, artifact["artifact_id"])
        if previous == structure_hash and not needs_reconciliation(artifact, desired_paths):
            page_ids = page_ids_for_paths(client, desired_paths)
            reconcile_catalogues(
                client,
                schema_catalogues_mappings(entities, page_ids, db_key),
                key_prefix=_key(structure_hash, "semantic"),
            )
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
                _key(structure_hash, "artifact-attribution"),
            )
            artifact = updated["artifact"]
            reconcile_catalogues(
                client,
                schema_catalogues_mappings(entities, page_ids, db_key),
                key_prefix=_key(structure_hash, "semantic"),
            )
            # Attribution and semantic receipts are the maintenance evidence;
            # the source fingerprint already has a successful generation.
            print(f"UNCHANGED {structure_hash[:16]} — updated generator attribution only")
            return 0

    artifact, page_ids = publish_pages(
        client,
        pages,
        structure_hash,
        include_presence=True,
        artifact=artifact,
        artifact_key=artifact_key,
        database_id=entities["database_id"],
    )
    reconcile_catalogues(
        client,
        schema_catalogues_mappings(entities, page_ids, db_key),
        key_prefix=_key(structure_hash, "semantic"),
    )
    emit_generation(
        client, artifact["artifact_id"], structure_hash,
        f"Regenerated {len(pages)} catalogue pages for {db_key} ({len(schemas)} schemas)",
    )
    print(
        f"PUBLISHED {len(page_ids)} pages, artifact {artifact['artifact_id']}, "
        f"fingerprint {structure_hash[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
