#!/usr/bin/env python3
"""Schema-catalogue projection: the structured document and its human views.

Pure and deterministic. Given the source structure produced by
``schema_catalogue_source.introspect`` (source projection contract 2), this
module builds:

  * the **structured projection** — the machine-readable schema graph DocPlane
    stores against the generated artifact and serves to agents at database,
    schema, table and column granularity (e004787b decision D1: detailed
    structure lives in a generator-owned projection, never as TABLE/COLUMN
    MODEL entities);
  * the **human projection** — one overview page per database and one page per
    schema, with table sections (never table pages) and Mermaid ER diagrams,
    including configured viewpoints for large schemas.

Both views are derived from the same structure in the same run, so the pages
can never describe a different schema from the one agents query.

Nothing here reads the environment, the network or a database, and nothing
here mutates anything. Rendering depends only on (structure, config, static
provenance), so an unchanged schema renders byte-identical pages; volatile
facts (extraction time, server version) live in the stored provenance, not
in page content, so they can never cause publication churn.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROJECTION_NAME = "docplane.schema-structure"
PROJECTION_KIND = "SCHEMA_STRUCTURE"

# A whole-schema diagram above this many tables is drawn relationship-only
# (primary keys only) and the configured viewpoints carry the key columns.
OVERVIEW_DETAIL_LIMIT = 20

_CANONICAL_STATUSES = {"AVAILABLE", "NOT_AVAILABLE", "NOT_YET_COMPARED"}


# ── Configuration ───────────────────────────────────────────────────────────

class ConfigError(ValueError):
    """A catalogue configuration file is structurally invalid."""


def load_config(path: Path | None) -> dict[str, Any]:
    """Load and validate one database's catalogue configuration.

    Absent file means an empty configuration: every schema is published as
    deployed introspection with canonical status ``NOT_YET_COMPARED`` and no
    viewpoints. The file is Git-tracked authority for presentation only; it
    can never add structure the source does not have.
    """
    if path is None or not path.exists():
        return {"schemas": {}}
    import yaml  # local import keeps the module importable without PyYAML

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a mapping")
    schemas = raw.get("schemas") or {}
    if not isinstance(schemas, dict):
        raise ConfigError("schemas must be a mapping")
    normalised: dict[str, Any] = {}
    for schema, spec in sorted(schemas.items()):
        spec = spec or {}
        canonical = spec.get("canonical") or {}
        status = canonical.get("status", "NOT_YET_COMPARED")
        if status not in _CANONICAL_STATUSES:
            raise ConfigError(f"{schema}: canonical.status must be one of {sorted(_CANONICAL_STATUSES)}")
        viewpoints = []
        for viewpoint in spec.get("viewpoints") or []:
            name = str(viewpoint.get("name", "")).strip()
            tables = viewpoint.get("tables") or []
            if not name or not isinstance(tables, list) or not tables:
                raise ConfigError(f"{schema}: every viewpoint needs a name and a non-empty table list")
            viewpoints.append(
                {
                    "name": name,
                    "description": str(viewpoint.get("description", "")).strip() or None,
                    "tables": [str(table) for table in tables],
                }
            )
        normalised[str(schema)] = {
            "description": str(spec.get("description", "")).strip() or None,
            "canonical": {
                "status": status,
                "reason": str(canonical.get("reason", "")).strip() or None,
                "source": str(canonical.get("source", "")).strip() or None,
            },
            "viewpoints": viewpoints,
        }
    return {"schemas": normalised}


def config_hash(config: dict[str, Any], static_provenance: dict[str, Any]) -> str:
    """Identity of everything besides structure that shapes rendered pages."""
    canonical = json.dumps(
        {"config": config, "provenance": static_provenance},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def schema_config(config: dict[str, Any], schema: str) -> dict[str, Any]:
    return config["schemas"].get(schema) or {
        "description": None,
        "canonical": {"status": "NOT_YET_COMPARED", "reason": None, "source": None},
        "viewpoints": [],
    }


# ── Structured projection ───────────────────────────────────────────────────

def relations(structure: dict[str, Any]) -> list[dict[str, Any]]:
    """Every foreign key as an explicit edge, cross-schema edges included."""
    edges = []
    for schema in sorted(structure):
        for table_name in sorted(structure[schema]["tables"]):
            table = structure[schema]["tables"][table_name]
            for fk in table["foreign_keys"]:
                target = fk["references"]
                edges.append(
                    {
                        "constraint": fk["name"],
                        "from": {"schema": schema, "table": table_name, "columns": fk["columns"]},
                        "to": {
                            "schema": target["schema"],
                            "table": target["table"],
                            "columns": target["columns"],
                        },
                        "on_delete": fk["on_delete"],
                        "on_update": fk["on_update"],
                        "cross_schema": target["schema"] != schema,
                        "target_catalogued": (
                            target["schema"] in structure
                            and target["table"] in structure[target["schema"]]["tables"]
                        ),
                    }
                )
    return edges


def build_document(
    db_key: str,
    db_display: str,
    structure: dict[str, Any],
    structure_hash: str,
    render_config_hash: str,
    contract_version: int,
) -> dict[str, Any]:
    """The machine-readable projection stored and served by DocPlane.

    ``schemas`` is the fingerprinted structure verbatim; DocPlane recomputes
    its fingerprint on write, so the stored document provably corresponds to
    the GENERATION evidence that follows it.
    """
    return {
        "projection": PROJECTION_NAME,
        "projection_contract_version": contract_version,
        "database": {"key": db_key, "display": db_display},
        "source_fingerprint": structure_hash,
        "config_hash": render_config_hash,
        "schemas": structure,
        "relations": relations(structure),
    }


def build_provenance(
    *,
    config: dict[str, Any],
    structure: dict[str, Any],
    static_provenance: dict[str, Any],
    source_metadata: dict[str, Any],
    extracted_at: str,
    generator: dict[str, Any],
) -> dict[str, Any]:
    """Where the structure came from and what it may be trusted to mean.

    Every schema is published as DEPLOYED introspection. Canonical-from-
    migrations availability is stated per schema from configuration and is
    never inferred from the deployed structure (decision D3). Migration-ledger
    identity is explicitly recorded as not captured in this contract.
    """
    schemas = {}
    for schema in sorted(structure):
        canonical = schema_config(config, schema)["canonical"]
        schemas[schema] = {"basis": "DEPLOYED_INTROSPECTION", "canonical": canonical}
    return {
        **static_provenance,
        **source_metadata,
        "extracted_at": extracted_at,
        "generator": generator,
        "schemas": schemas,
        "migration_ledger": {
            "status": "NOT_CAPTURED",
            "reason": (
                "Ledger identity requires a row read of the migration ledger; the "
                "least-privilege source contract reads catalogue metadata only."
            ),
        },
    }


# ── Human projection: Markdown + Mermaid ────────────────────────────────────

_MERMAID_TOKEN = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(name: str) -> str:
    token = _MERMAID_TOKEN.sub("_", name)
    return token if token[:1].isalpha() else f"t_{token}"


def _mermaid_type(column: dict[str, Any]) -> str:
    base = column["udt"].split(".")[-1].lstrip("_") or "unknown"
    token = _MERMAID_TOKEN.sub("_", base)
    if not token[:1].isalpha():
        token = f"t_{token}"
    return f"{token}_array" if column["array"] else token


def _key_columns(table: dict[str, Any]) -> dict[str, str]:
    """Column name -> Mermaid key marker for PK/FK/UK columns."""
    markers: dict[str, list[str]] = {}
    for column in (table.get("primary_key") or {}).get("columns", []):
        markers.setdefault(column, []).append("PK")
    for fk in table.get("foreign_keys", []):
        for column in fk["columns"]:
            if "FK" not in markers.setdefault(column, []):
                markers[column].append("FK")
    for unique in table.get("unique", []):
        for column in unique["columns"]:
            if "UK" not in markers.setdefault(column, []) and "PK" not in markers[column]:
                markers[column].append("UK")
    return {column: ",".join(values) for column, values in markers.items()}


def _entity_block(entity: str, table: dict[str, Any], *, detail: str) -> list[str]:
    """``detail``: "keys" (PK/FK/UK columns) or "pk" (primary key only)."""
    markers = _key_columns(table)
    lines = [f"    {entity} {{"]
    emitted = 0
    for column in table["columns"]:
        marker = markers.get(column["name"])
        if detail == "pk" and (not marker or "PK" not in marker):
            continue
        if detail == "keys" and not marker:
            continue
        suffix = f" {marker}" if marker else ""
        lines.append(f"        {_mermaid_type(column)} {_mermaid_id(column['name'])}{suffix}")
        emitted += 1
    if emitted == 0 and table["columns"]:
        # An empty entity block is not portable across Mermaid versions.
        first = table["columns"][0]
        lines.append(f"        {_mermaid_type(first)} {_mermaid_id(first['name'])}")
    lines.append("    }")
    return lines


def _edge(parent: str, child: str, fk: dict[str, Any], child_table: dict[str, Any]) -> str:
    """One FK as a Mermaid relationship, in word-form cardinality.

    The symbolic crow's foot (``||--o{``) carries an unmatched brace, and the
    canonical redaction transform guarantees brace balance for every published
    document. Mermaid's word aliases express the same cardinality without one.
    """
    nullable = {c["name"]: c["nullable"] for c in child_table["columns"]}
    optional = any(nullable.get(column, True) for column in fk["columns"])
    parent_side = "zero or one" if optional else "only one"
    label = ", ".join(fk["columns"]) or fk["name"]
    return f'    {parent} {parent_side} to zero or more {child} : "{label}"'


def mermaid_schema(
    schema: str,
    structure: dict[str, Any],
    tables: list[str] | None = None,
    *,
    detail: str = "keys",
) -> str:
    """ER diagram for ``tables`` of ``schema`` (all tables when None).

    Edges to tables outside the selection are drawn to a primary-key-only
    external entity named ``<schema>__<table>`` so a viewpoint shows where it
    connects to the rest of the database without pulling it in wholesale.
    """
    own = structure[schema]["tables"]
    selected = sorted(own) if tables is None else [name for name in tables if name in own]
    entities: dict[str, list[str]] = {}
    edges: list[str] = []

    def entity_for(target_schema: str, target_table: str) -> str | None:
        if target_schema == schema and target_table in selected:
            return _mermaid_id(target_table)
        target = structure.get(target_schema, {}).get("tables", {}).get(target_table)
        entity = _mermaid_id(f"{target_schema}__{target_table}")
        if entity not in entities:
            entities[entity] = (
                _entity_block(entity, target, detail="pk") if target is not None
                else [f"    {entity} {{", "        external ref", "    }"]
            )
        return entity

    for name in selected:
        entities[_mermaid_id(name)] = _entity_block(_mermaid_id(name), own[name], detail=detail)
    for name in selected:
        for fk in own[name]["foreign_keys"]:
            ref = fk["references"]
            parent = entity_for(ref["schema"], ref["table"])
            edges.append(_edge(parent, _mermaid_id(name), fk, own[name]))
    # For a whole-schema diagram, inbound edges from other schemas belong to
    # the database overview's cross-schema diagram. For a selection, draw the
    # same-schema tables that point into it as primary-key-only externals.
    if tables is not None:
        for name in sorted(own):
            if name in selected:
                continue
            for fk in own[name]["foreign_keys"]:
                ref = fk["references"]
                if ref["schema"] == schema and ref["table"] in selected:
                    child = entity_for(schema, name)
                    edges.append(_edge(_mermaid_id(ref["table"]), child, fk, own[name]))
    lines = ["```mermaid", "erDiagram"]
    for entity in sorted(entities):
        lines += entities[entity]
    lines += sorted(set(edges))
    lines.append("```")
    return "\n".join(lines)


def mermaid_cross_schema(structure: dict[str, Any]) -> str | None:
    """Only the tables that participate in cross-schema foreign keys."""
    cross = [edge for edge in relations(structure) if edge["cross_schema"]]
    if not cross:
        return None
    entities: dict[str, list[str]] = {}
    edges = []
    for edge in cross:
        child_schema, child_table = edge["from"]["schema"], edge["from"]["table"]
        parent_schema, parent_table = edge["to"]["schema"], edge["to"]["table"]
        child = _mermaid_id(f"{child_schema}__{child_table}")
        parent = _mermaid_id(f"{parent_schema}__{parent_table}")
        child_def = structure[child_schema]["tables"][child_table]
        entities.setdefault(child, _entity_block(child, child_def, detail="keys"))
        parent_def = structure.get(parent_schema, {}).get("tables", {}).get(parent_table)
        entities.setdefault(
            parent,
            _entity_block(parent, parent_def, detail="pk") if parent_def is not None
            else [f"    {parent} {{", "        external ref", "    }"],
        )
        fk = next(fk for fk in child_def["foreign_keys"] if fk["name"] == edge["constraint"])
        edges.append(_edge(parent, child, fk, child_def))
    lines = ["```mermaid", "erDiagram"]
    for entity in sorted(entities):
        lines += entities[entity]
    lines += sorted(set(edges))
    lines.append("```")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _code(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"`{_cell(value)}`"


def _inbound(structure: dict[str, Any], schema: str, table: str) -> list[dict[str, Any]]:
    return [
        edge for edge in relations(structure)
        if edge["to"]["schema"] == schema and edge["to"]["table"] == table
    ]


def _table_section(schema: str, name: str, table: dict[str, Any], structure: dict[str, Any]) -> list[str]:
    markers = _key_columns(table)
    lines = [f"### `{name}`", ""]
    if table["comment"]:
        lines += [_cell(table["comment"]), ""]
    if table["kind"] != "table":
        lines += [f"Kind: {table['kind'].replace('_', ' ')}", ""]
    lines += ["| Column | Type | Nullable | Default | Key | Notes |", "| --- | --- | --- | --- | --- | --- |"]
    for column in table["columns"]:
        notes = []
        if column["enum"]:
            notes.append(f"enum `{column['enum']}`")
        if column["identity"]:
            notes.append(f"identity {column['identity']}")
        if column["generated"]:
            notes.append(f"generated `{_cell(column['generated'])}`")
        if column["comment"]:
            notes.append(_cell(column["comment"]))
        lines.append(
            f"| `{column['name']}` | {_cell(column['type'])} | "
            f"{'yes' if column['nullable'] else 'no'} | {_code(column['default'])} | "
            f"{markers.get(column['name'], '')} | {'; '.join(notes)} |"
        )
    lines.append("")
    if table.get("primary_key"):
        pk = table["primary_key"]
        lines += [f"Primary key: `{pk['name']}` ({', '.join(f'`{c}`' for c in pk['columns'])})", ""]
    if table.get("foreign_keys"):
        lines.append("Foreign keys:")
        for fk in table["foreign_keys"]:
            ref = fk["references"]
            lines.append(
                f"- `{fk['name']}` — ({', '.join(fk['columns'])}) → "
                f"`{ref['schema']}.{ref['table']}` ({', '.join(ref['columns'])}); "
                f"on delete {fk['on_delete']}, on update {fk['on_update']}"
            )
        lines.append("")
    inbound = _inbound(structure, schema, name)
    if inbound:
        lines.append("Referenced by:")
        for edge in inbound:
            source = edge["from"]
            lines.append(
                f"- `{source['schema']}.{source['table']}` ({', '.join(source['columns'])}) "
                f"via `{edge['constraint']}`"
            )
        lines.append("")
    if table.get("unique"):
        lines.append("Unique constraints:")
        lines += [f"- `{u['name']}` ({', '.join(u['columns'])})" for u in table["unique"]]
        lines.append("")
    if table.get("checks"):
        lines.append("Check constraints:")
        lines += [f"- `{c['name']}` — `{_cell(c['definition'])}`" for c in table["checks"]]
        lines.append("")
    if table.get("exclusions"):
        lines.append("Exclusion constraints:")
        lines += [f"- `{x['name']}` — `{_cell(x['definition'])}`" for x in table["exclusions"]]
        lines.append("")
    if table.get("indexes"):
        lines.append("Indexes:")
        for index in table["indexes"]:
            flags = [index["method"]]
            if index["primary"]:
                flags.append("primary")
            elif index["unique"]:
                flags.append("unique")
            detail = f"- `{index['name']}` — {' '.join(flags)} ({', '.join(_cell(c) for c in index['columns'])})"
            if index["include"]:
                detail += f" include ({', '.join(index['include'])})"
            if index["predicate"]:
                detail += f" where `{_cell(index['predicate'])}`"
            lines.append(detail)
        lines.append("")
    return lines


def _view_section(name: str, view: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}` ({view['kind'].replace('_', ' ')})", ""]
    if view["comment"]:
        lines += [_cell(view["comment"]), ""]
    lines += ["| Column | Type |", "| --- | --- |"]
    lines += [f"| `{c['name']}` | {_cell(c['type'])} |" for c in view["columns"]]
    lines += ["", "Definition:", "", "```sql", view["definition"].strip(), "```", ""]
    return lines


def _stamp(generator: dict[str, Any], structure_hash: str) -> str:
    return (
        f"> Generated by `{generator['name']}` {generator['version']} · "
        f"projection contract {generator['projection_contract_version']} · "
        f"source fingerprint `{structure_hash[:16]}` · structure only, no row data. "
        "Edit through the generator, never by hand."
    )


def render_pages(
    *,
    section: str,
    lifecycle_lines: tuple[str, ...],
    db_key: str,
    db_display: str,
    structure: dict[str, Any],
    structure_hash: str,
    config: dict[str, Any],
    static_provenance: dict[str, Any],
    generator: dict[str, Any],
) -> list[dict[str, str]]:
    """One overview page per database plus one page per schema.

    Deterministic for (structure, config, static provenance, generator): no
    clock, no server version, no row data. Redaction is applied by the caller.
    """
    stamp = _stamp(generator, structure_hash)
    pages: list[dict[str, str]] = []
    inventory = [
        "| Schema | Tables | Views | Enums | Basis | Canonical from migrations |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for schema in sorted(structure):
        spec = schema_config(config, schema)
        body = structure[schema]
        canonical = spec["canonical"]
        inventory.append(
            f"| [`{schema}`]({schema}.md) | {len(body['tables'])} | {len(body['views'])} | "
            f"{len(body['enums'])} | deployed introspection | {canonical['status'].replace('_', ' ').lower()} |"
        )
        lines = [f"# {db_display} — `{schema}`", "", *lifecycle_lines, "", stamp, ""]
        if spec["description"]:
            lines += [spec["description"], ""]
        if body["comment"]:
            lines += [_cell(body["comment"]), ""]
        lines += [
            "## Provenance",
            "",
            f"- Basis: **deployed introspection** of `{db_key}` "
            f"({_cell(static_provenance.get('environment'))}, "
            f"source `{_cell(static_provenance.get('source_identity'))}`).",
            f"- Canonical from migrations: **{canonical['status'].replace('_', ' ').lower()}**"
            + (f" — {_cell(canonical['reason'])}" if canonical["reason"] else "")
            + (f" Source: {_cell(canonical['source'])}." if canonical["source"] else ""),
            "- Agents: query this schema through the DocPlane structure API or the "
            "`schema_describe` / `schema_find_column` / `schema_relations` MCP tools "
            "rather than parsing this page.",
            "",
        ]
        tables = body["tables"]
        if tables:
            detail = "keys" if len(tables) <= OVERVIEW_DETAIL_LIMIT else "pk"
            related = sorted(
                {name for name, t in tables.items() if t["foreign_keys"]}
                | {
                    edge["to"]["table"] for edge in relations(structure)
                    if edge["to"]["schema"] == schema and edge["from"]["schema"] == schema
                }
            )
            isolated = sorted(set(tables) - set(related))
            lines += ["## Entity relationships", ""]
            if related:
                lines += [mermaid_schema(schema, structure, related if isolated else None, detail=detail), ""]
            if isolated:
                lines += [
                    "Tables with no foreign-key relationship inside this schema: "
                    + ", ".join(f"[`{name}`](#{name})" for name in isolated),
                    "",
                ]
            for viewpoint in spec["viewpoints"]:
                present = [name for name in viewpoint["tables"] if name in tables]
                missing = [name for name in viewpoint["tables"] if name not in tables]
                lines += [f"### Viewpoint: {viewpoint['name']}", ""]
                if viewpoint["description"]:
                    lines += [viewpoint["description"], ""]
                if present:
                    lines += [mermaid_schema(schema, structure, present, detail="keys"), ""]
                if missing:
                    lines += [
                        "Configured but absent from the deployed schema: "
                        + ", ".join(f"`{name}`" for name in missing),
                        "",
                    ]
            lines += ["## Tables", ""]
            for name in sorted(tables):
                lines += _table_section(schema, name, tables[name], structure)
        if body["views"]:
            lines += ["## Views", ""]
            for name in sorted(body["views"]):
                lines += _view_section(name, body["views"][name])
        if body["enums"]:
            lines += ["## Enumerated types", ""]
            for name in sorted(body["enums"]):
                labels = ", ".join(f"`{label}`" for label in body["enums"][name])
                lines.append(f"- `{schema}.{name}`: {labels}")
            lines.append("")
        pages.append(
            {
                "path": f"{section}/{db_key}/{schema}.md",
                "title": f"{db_display} — {schema}",
                "nav_path": f"Model / Schema catalogue / {db_display} / {schema}",
                "content": "\n".join(lines).rstrip() + "\n",
            }
        )
    overview = [
        f"# {db_display} schema catalogue",
        "",
        *lifecycle_lines,
        "",
        stamp,
        "",
        "## Provenance",
        "",
        f"- Database identity: `{db_key}` (MODEL `DATABASE`), "
        f"environment **{_cell(static_provenance.get('environment'))}**, "
        f"source `{_cell(static_provenance.get('source_identity'))}`.",
        "- Basis: deployed-schema introspection, structure only, read from the PostgreSQL "
        "catalogue by a read-only transaction.",
        "- Migration-ledger identity: not captured by this projection contract.",
        "- Extraction time, server version and live freshness (observer evidence and "
        "projection correspondence) are served by `schema_provenance`, not frozen into this page.",
        "",
        "## Schemas",
        "",
        *inventory,
        "",
    ]
    cross = mermaid_cross_schema(structure)
    if cross:
        overview += ["## Cross-schema relationships", "", cross, ""]
    pages.insert(
        0,
        {
            "path": f"{section}/{db_key}/index.md",
            "title": f"{db_display} schema catalogue",
            "nav_path": f"Model / Schema catalogue / {db_display} / Overview",
            "content": "\n".join(overview).rstrip() + "\n",
        },
    )
    return pages
