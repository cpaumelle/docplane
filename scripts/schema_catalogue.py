#!/usr/bin/env python3
"""Schema catalogue generator — the Sprint 5 exemplar (DOMAIN_MODEL.md, A).

Reads STRUCTURE-ONLY metadata from a source PostgreSQL, passes every rendered
document through the canonical redaction transform (fail-closed), and drives
the DocPlane API as a named AUTOMATION principal:

  model    DATABASE + SCHEMA entities, STORES_IN wires
  know     catalogue pages behind a permanent presence page,
           published through change -> validate -> publish
  model    one artifact declaration binding the catalogue pages' stable
           resource identities (ownership + publication guard)
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

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from migration.redaction import redact  # noqa: E402

GENERATOR_NAME = "docplane-schema-catalogue"
GENERATOR_VERSION = "1.0.0"
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
        schema_lines.append(
            f"- [`{schema}`]({db_key}/{schema}.md) — {len(tables)} tables"
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
    pages.insert(
        0,
        {
            "path": f"{SECTION}/{db_key}/index.md",
            "title": f"{db_display} schema catalogue",
            "nav_path": f"Model / Schema catalogue / {db_display}",
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
        "nav_path": "Model / Schema catalogue",
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
    return f"schema-catalogue-{structure_hash[:16]}-{verb}{'-' + discriminator if discriminator else ''}"[:256]


# ── The run ─────────────────────────────────────────────────────────────────

def ensure_entities(client: Client, db_key: str, db_display: str, schemas: list[str], structure_hash: str) -> str:
    """DATABASE + SCHEMA entities with STORES_IN wires. Returns database entity id."""
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
        for entity in client.call("GET", "/api/v1/model/entities?entity_kind=SCHEMA").get("entities", [])
    }
    for schema in sorted(schemas):
        schema_key = f"{db_key}.{schema}"
        if schema_key in schema_entities:
            continue
        schema_id = client.call(
            "POST", "/api/v1/model/entities",
            {"entity_kind": "SCHEMA", "entity_key": schema_key, "display_name": f"{db_display} {schema}"},
            _key(structure_hash, "entity", schema_key),
        )["entity_id"]
        client.call(
            "POST", f"/api/v1/model/entities/{schema_id}/links",
            {"relation": "STORES_IN", "target_entity_id": database_id},
            _key(structure_hash, "link", schema_key),
        )
    return database_id


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


def publish_pages(client: Client, pages: list[dict[str, str]], structure_hash: str, include_presence: bool) -> dict[str, str]:
    """One change carrying CREATE_PAGE / REPLACE_DOCUMENT per page.
    Returns path -> resource_id for every catalogue page."""
    # The pages listing filters by EXACT path, so resolve each wanted path
    # individually rather than assuming a prefix listing exists.
    def lookup(path: str) -> dict[str, Any] | None:
        found = client.call("GET", f"/api/v1/pages?path={path}").get("pages", [])
        return found[0] if found else None

    existing: dict[str, dict[str, Any]] = {}
    for page in pages:
        current = lookup(page["path"])
        if current is not None:
            existing[page["path"]] = current
    wanted = list(pages)
    if include_presence and lookup(PRESENCE_PATH) is None:
        wanted.append(presence_page())
    operations = []
    for page in wanted:
        current = existing.get(page["path"])
        if current is None:
            operations.append(("CREATE_PAGE", None, None, page))
        elif current["path"] == PRESENCE_PATH:
            continue  # permanent presence page is never regenerated
        else:
            operations.append(("REPLACE_DOCUMENT", current["resource_id"], current["revision"], page))
    if not operations:
        return {page["path"]: existing[page["path"]]["resource_id"] for page in pages}
    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Schema catalogue regeneration {structure_hash[:16]}",
            "purpose": (
                "Fingerprint-bound regeneration by the schema-catalogue "
                f"generator; source structural fingerprint {structure_hash}."
            ),
            "workspace_key": "reference",
        },
        _key(structure_hash, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        payload: dict[str, Any] = {
            "path": page["path"], "title": page["title"],
            "nav_path": page["nav_path"], "content": page["content"],
        }
        request: dict[str, Any] = {"operation_type": operation_type, "payload": payload}
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call(
            "POST", f"/api/v1/changes/{change_id}/operations", request,
            _key(structure_hash, "operation", page["path"]),
        )
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(structure_hash, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(structure_hash, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")
    resolved = {page["path"]: lookup(page["path"]) for page in pages}
    missing = [path for path, found in resolved.items() if found is None]
    if missing:
        raise RuntimeError(f"published pages not found after publication: {missing}")
    return {path: found["resource_id"] for path, found in resolved.items()}


def ensure_artifact(client: Client, db_key: str, database_id: str, page_ids: dict[str, str], structure_hash: str) -> str:
    artifact_key = f"schema-catalogue-{db_key}"
    artifact = current_artifact(client, artifact_key)
    if artifact is None:
        artifact = client.call(
            "POST", "/api/v1/model/artifacts",
            {
                "artifact_key": artifact_key,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "source_entity_id": database_id,
                "redaction_policy": "canonical",
                "target_page_resource_ids": sorted(page_ids.values()),
                "target_page_paths": sorted(page_ids),
            },
            _key(structure_hash, "artifact", db_key),
        )
    return artifact["artifact_id"]


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
    database_id = ensure_entities(client, db_key, db_display, schemas, structure_hash)
    artifact = current_artifact(client, f"schema-catalogue-{db_key}")
    if artifact is not None:
        previous = last_generation_fingerprint(client, artifact["artifact_id"])
        if previous == structure_hash:
            print(f"UNCHANGED {structure_hash[:16]} — nothing to regenerate")
            return 0
    page_ids = publish_pages(client, pages, structure_hash, include_presence=True)
    artifact_id = ensure_artifact(client, db_key, database_id, page_ids, structure_hash)
    emit_generation(
        client, artifact_id, structure_hash,
        f"Regenerated {len(pages)} catalogue pages for {db_key} ({len(schemas)} schemas)",
    )
    print(f"PUBLISHED {len(page_ids)} pages, artifact {artifact_id}, fingerprint {structure_hash[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
