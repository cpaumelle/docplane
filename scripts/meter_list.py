#!/usr/bin/env python3
"""Monitoring meter-list importer — the Sprint 6 exemplar (DOMAIN_MODEL.md, B).

Prometheus holds the readings; DocPlane holds the meter list. This importer
reads alerting/recording rule files from a git checkout of the monitoring
configuration (charliehub-hub2: monitoring/prometheus/rules/), and drives the
DocPlane API as a named AUTOMATION principal:

  model    one MONITOR_RULE entity per rule, WATCHES-wired to the SERVICE
           entity named by the rule's service label
  know     fingerprint-bound plain-English explanation pages behind a
           permanent presence page, published through the governed flow
  model    one artifact declaration binding the generated pages
  observe  a GENERATION observation carrying the rule-set fingerprint

Coverage is populated as gaps, never stubs: rules without descriptions carry
has_description=false, and services with no WATCHES edge surface through
GET /api/v1/observe/coverage.

Environment:
  DOCPLANE_API               routed front
  DOCPLANE_METER_LIST_TOKEN  AUTOMATION bearer (never logged)
  METER_RULES_DIR            Prometheus rules directory in the checkout,
                             e.g. /opt/charliehub/monitoring/prometheus/rules
  METER_SOURCE_KEY           entity key for the monitoring source
                             (default hub2.prometheus)

Usage: meter_list.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from migration.redaction import redact  # noqa: E402
from schema_catalogue import ApiError, Client  # noqa: E402  (shared API client)

GENERATOR_NAME = "docplane-meter-list"
GENERATOR_VERSION = "1.1.0"
SECTION = "observe/meter-list"
PRESENCE_PATH = f"{SECTION}/index.md"

_SLUG_RE = re.compile(r"[^a-z0-9_.-]+")


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-.")
    return slug[:120] or "unnamed"


# ── Parsing: the rule files are the source of truth ─────────────────────────

def parse_rules(rules_dir: Path) -> dict[str, Any]:
    """Deterministic structure: file stem -> group name -> [rules].

    Only rule content is captured — no runtime data, no secrets expected in
    rule files, and rendering is still redaction-gated downstream."""
    structure: dict[str, Any] = {}
    for path in sorted(rules_dir.glob("*.yml")) + sorted(rules_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        groups: dict[str, list[dict[str, Any]]] = {}
        for group in document.get("groups") or []:
            rules = []
            for rule in group.get("rules") or []:
                name = rule.get("alert") or rule.get("record")
                if not name:
                    continue
                annotations = rule.get("annotations") or {}
                labels = rule.get("labels") or {}
                rules.append(
                    {
                        "name": str(name),
                        "rule_kind": "alert" if "alert" in rule else "record",
                        "expr": str(rule.get("expr", "")).strip(),
                        "pending_for": str(rule["for"]) if "for" in rule else None,
                        "severity": labels.get("severity"),
                        "service": labels.get("service"),
                        "summary": annotations.get("summary"),
                        "description": annotations.get("description"),
                        "runbook_url": annotations.get("runbook_url"),
                    }
                )
            if rules:
                groups[str(group.get("name", "unnamed"))] = rules
        if groups:
            structure[path.stem] = groups
    return structure


def fingerprint(structure: dict[str, Any]) -> str:
    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def iter_rules(structure: dict[str, Any]):
    for file_stem in sorted(structure):
        for group_name in sorted(structure[file_stem]):
            for rule in structure[file_stem][group_name]:
                yield file_stem, group_name, rule


# ── Rendering: plain-English explanations, redaction-gated ──────────────────

def _brace_safe(text: str) -> str:
    """Rule prose may legitimately carry unbalanced braces (grep patterns,
    template fragments — a real hub2 rule does), which the canonical
    redaction transform's brace invariant refuses. HTML entities render
    identically in the published page while keeping the document brace-free
    for the transform. PromQL stays verbatim in fences — selectors and
    templates are balanced there by construction."""
    return text.replace("{", "&#123;").replace("}", "&#125;")


def _rule_section(rule: dict[str, Any]) -> list[str]:
    watches = f" · watches `{rule['service']}`" if rule.get("service") else ""
    severity = f" · severity {rule['severity']}" if rule.get("severity") else ""
    pending = f" · pending {rule['pending_for']}" if rule.get("pending_for") else ""
    lines = [f"### `{rule['name']}`", "", f"{rule['rule_kind']}{watches}{severity}{pending}", ""]
    if rule.get("summary"):
        lines += [f"**{_brace_safe(rule['summary'].strip())}**", ""]
    if rule.get("description"):
        lines += [_brace_safe(rule["description"].strip()), ""]
    else:
        lines += ["_No description in the rule file — surfaced as a coverage gap, not papered over._", ""]
    if rule.get("runbook_url"):
        lines += [f"**Runbook**: <{rule['runbook_url'].strip()}>", ""]
    lines += ["```promql", rule["expr"], "```", ""]
    return lines


def render_pages(source_key: str, structure: dict[str, Any], structure_hash: str) -> list[dict[str, str]]:
    stamp = (
        f"> Generated by `{GENERATOR_NAME}` {GENERATOR_VERSION} · "
        f"rule-set fingerprint `{structure_hash[:16]}` · the rule files in git are "
        "authoritative. Edit rules there, never here."
    )
    pages: list[dict[str, str]] = []
    index_lines = []
    for file_stem in sorted(structure):
        groups = structure[file_stem]
        rule_count = sum(len(rules) for rules in groups.values())
        index_lines.append(f"- [`{file_stem}`]({source_key}/{file_stem}.md) — {rule_count} rules")
        body = [f"# Meter list — `{file_stem}`", "", stamp, ""]
        for group_name in sorted(groups):
            body += [f"## Group `{group_name}`", ""]
            for rule in groups[group_name]:
                body += _rule_section(rule)
        pages.append(
            {
                "path": f"{SECTION}/{source_key}/{file_stem}.md",
                "title": f"Meter list — {file_stem}",
                "nav_path": f"Observe / Meter list / {source_key} / {file_stem}",
                "content": "\n".join(body).rstrip() + "\n",
            }
        )
    total_rules = sum(len(rules) for groups in structure.values() for rules in groups.values())
    overview = [
        f"# {source_key} meter list",
        "",
        stamp,
        "",
        f"{total_rules} rules across {len(structure)} rule files:",
        "",
        *index_lines,
        "",
    ]
    pages.insert(
        0,
        {
            "path": f"{SECTION}/{source_key}/index.md",
            "title": f"{source_key} meter list",
            # The source node parents the per-file pages, so its landing page
            # takes the corpus's Overview-leaf idiom (Sprint 5 nav lesson).
            "nav_path": f"Observe / Meter list / {source_key} / Overview",
            "content": "\n".join(overview),
        },
    )
    for page in pages:
        if page["content"].count("{") != page["content"].count("}"):
            # Only an unbalanced PromQL expression can reach here (prose is
            # entity-escaped). Fail loudly and name the page rather than let
            # the transform's internal invariant blame itself.
            raise RuntimeError(f"unbalanced braces in rendered page {page['path']} — inspect its rule expressions")
        page["content"] = redact(page["content"], label="meter-list").sanitised
    return pages


def presence_page() -> dict[str, str]:
    return {
        "path": PRESENCE_PATH,
        "title": "Meter list",
        "nav_path": "Observe / Meter list / Overview",
        "content": (
            "# Meter list\n\n"
            "Prometheus holds the readings; DocPlane holds the meter list. "
            "Pages under this section are generated from the monitoring "
            f"configuration in git by the `{GENERATOR_NAME}` AUTOMATION "
            "principal and regenerate only when the rule-set fingerprint "
            "changes.\n\n"
            "This presence page is permanent and hand-curated: it survives "
            "every regeneration and is the stable place for ownership notes "
            "and the coverage-review runbook link.\n"
        ),
    }


def _key(structure_hash: str, verb: str, discriminator: str = "") -> str:
    return (
        f"meter-list-{GENERATOR_VERSION}-{structure_hash[:16]}-{verb}"
        f"{'-' + discriminator if discriminator else ''}"
    )[:256]


# ── The run ─────────────────────────────────────────────────────────────────

def rule_attributes(file_stem: str, group_name: str, rule: dict[str, Any]) -> dict[str, Any]:
    attributes = {
        "rule_kind": rule["rule_kind"],
        "expr": rule["expr"],
        "source_file": f"{file_stem}.yml",
        "group": group_name,
        "has_description": bool(rule.get("description")),
    }
    for field in ("severity", "pending_for", "runbook_url"):
        if rule.get(field):
            attributes[field] = rule[field]
    return attributes


# A rule set that suddenly retires more than this share of the existing meter
# list is far more likely a bad checkout (wrong branch, truncated clone) than
# a real mass decommission. The floor keeps small meter lists workable.
MASS_RETIREMENT_FLOOR = 5
MASS_RETIREMENT_SHARE = 0.2


def reconcile_entities(
    client: Client,
    source_key: str,
    structure: dict[str, Any],
    structure_hash: str,
    *,
    allow_mass_retirement: bool = False,
) -> dict[str, Any]:
    """Full lifecycle reconciliation against the rule files (which are
    authoritative — DOMAIN_MODEL.md, exemplar B), not first-run seeding:

      create      rules new to the meter list
      update      rules whose harvested attributes or display name moved
      rewire      WATCHES edges replaced when a service label changes or drops
      retire      rules no longer present in any rule file, bounded by an
                  explicit mass-retirement guard
      reactivate  retired rules restored in git — (kind, key) is unique
                  across statuses, so identity resumes, never duplicates

    Creates and link-adds are idempotent server-side (ON CONFLICT), the
    lifecycle verbs are versioned CAS calls keyed by fingerprint, so a
    resumed run converges — the Sprint 5 resume lesson, now for every verb.
    Returns the source entity id plus a reconciliation summary."""
    def list_all(query: str) -> list[dict[str, Any]]:
        # A reconciler must never act on a truncated world-view: a hidden
        # entity would read as "new" (create → 409 crash) or escape
        # retirement. Fail closed instead of converging on partial state.
        listing = client.call("GET", f"/api/v1/model/entities?{query}&limit=1000")
        if listing.get("truncated"):
            raise RuntimeError(
                f"entity listing truncated ({listing.get('count')}/{listing.get('total')} for {query}) — "
                "refusing to reconcile against a partial meter list"
            )
        return listing.get("entities", [])

    services = {entity["entity_key"]: entity for entity in list_all("entity_kind=SERVICE")}
    rules = {entity["entity_key"]: entity for entity in list_all("entity_kind=MONITOR_RULE&status=all")}
    summary = {"created": 0, "updated": 0, "retired": 0, "reactivated": 0, "links_added": 0, "links_removed": 0}

    def ensure_service(key: str, display: str) -> str:
        if key not in services:
            services[key] = client.call(
                "POST", "/api/v1/model/entities",
                {"entity_kind": "SERVICE", "entity_key": key, "display_name": display},
                _key(structure_hash, "entity", key),
            )
        return services[key]["entity_id"]

    source_id = ensure_service(source_key, f"{source_key} (monitoring source)")

    desired: dict[str, dict[str, Any]] = {}
    for file_stem, group_name, rule in iter_rules(structure):
        rule_key = f"rule.{_slug(rule['name'])}"
        desired[rule_key] = {
            "display_name": rule["name"],
            "attributes": rule_attributes(file_stem, group_name, rule),
            "service": rule.get("service"),
        }

    # Retirement first, so a renamed rule can never trip over its old key.
    # The importer owns the MONITOR_RULE kind outright, so the bound is the
    # kind itself — nothing outside it is ever touched.
    active_count = sum(1 for entity in rules.values() if entity["status"] == "ACTIVE")
    to_retire = [key for key in sorted(rules) if key not in desired and rules[key]["status"] == "ACTIVE"]
    threshold = max(MASS_RETIREMENT_FLOOR, int(active_count * MASS_RETIREMENT_SHARE))
    if len(to_retire) > threshold and not allow_mass_retirement:
        raise RuntimeError(
            f"refusing to retire {len(to_retire)} of {active_count} meter-list rules "
            f"(bound {threshold}) — verify the checkout is the real rule set, then "
            "rerun with --allow-mass-retirement"
        )
    for rule_key in to_retire:
        entity = rules[rule_key]
        client.call(
            "POST", f"/api/v1/model/entities/{entity['entity_id']}/retire",
            {"expected_version": entity["version"], "note": f"absent from rule set {structure_hash[:16]}"},
            _key(structure_hash, "retire", rule_key),
        )
        summary["retired"] += 1
        print(f"RETIRED {rule_key} — no longer in any rule file")

    for rule_key in sorted(desired):
        wanted = desired[rule_key]
        existing = rules.get(rule_key)
        if existing is not None and existing["status"] == "RETIRED":
            # The rule came back to git: its identity resumes.
            existing = rules[rule_key] = client.call(
                "POST", f"/api/v1/model/entities/{existing['entity_id']}/reactivate",
                {"expected_version": existing["version"], "note": f"restored in rule set {structure_hash[:16]}"},
                _key(structure_hash, "reactivate", rule_key),
            )
            summary["reactivated"] += 1
            print(f"REACTIVATED {rule_key} — restored in the rule files")
        if existing is None:
            rules[rule_key] = client.call(
                "POST", "/api/v1/model/entities",
                {"entity_kind": "MONITOR_RULE", "entity_key": rule_key, "display_name": wanted["display_name"], "attributes": wanted["attributes"]},
                _key(structure_hash, "entity", rule_key),
            )
            summary["created"] += 1
            stale_links = []
        else:
            if existing["attributes"] != wanted["attributes"] or existing["display_name"] != wanted["display_name"]:
                rules[rule_key] = client.call(
                    "POST", f"/api/v1/model/entities/{existing['entity_id']}/update",
                    {"expected_version": existing["version"], "display_name": wanted["display_name"], "attributes": wanted["attributes"]},
                    _key(structure_hash, "update", rule_key),
                )
                summary["updated"] += 1
            # Only a pre-existing rule can carry stale wires.
            detail = client.call("GET", f"/api/v1/model/entities/{existing['entity_id']}")
            stale_links = [link for link in detail.get("links_out", []) if link["relation"] == "WATCHES"]

        rule_id = rules[rule_key]["entity_id"]
        wanted_service_ids = set()
        if wanted["service"]:
            wanted_service_ids.add(ensure_service(_slug(wanted["service"]), wanted["service"]))
        for service_id in sorted(wanted_service_ids):
            if not any(link["to_entity_id"] == service_id for link in stale_links):
                client.call(
                    "POST", f"/api/v1/model/entities/{rule_id}/links",
                    {"relation": "WATCHES", "to_entity_id": service_id},
                    _key(structure_hash, "link", rule_key),
                )
                summary["links_added"] += 1
        for link in stale_links:
            if link["to_entity_id"] not in wanted_service_ids:
                client.call(
                    "POST", f"/api/v1/model/entities/{rule_id}/links/remove",
                    {"relation": "WATCHES", "to_entity_id": link["to_entity_id"], "note": f"service label moved in rule set {structure_hash[:16]}"},
                    _key(structure_hash, "unlink", f"{rule_key}-{link['entity_key']}"),
                )
                summary["links_removed"] += 1

    return {"source_id": source_id, **summary}


def artifact_needs_succession(artifact: dict[str, Any], desired_paths: list[str]) -> bool:
    """The declaration must mirror reality: a target-set change (rule file
    added/removed) or a generator-contract change (version bump) makes the
    standing declaration stale — new pages would sit unprotected and removed
    pages would stay owned. Migration 007 exists exactly so the successor
    can be declared under the same key."""
    return (
        sorted(artifact.get("target_page_paths") or []) != sorted(desired_paths)
        or artifact.get("generator_version") != GENERATOR_VERSION
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-mass-retirement", action="store_true",
        help="permit retiring more than the mass-retirement bound in one run",
    )
    args = parser.parse_args(argv)

    rules_dir = Path(os.environ["METER_RULES_DIR"])
    source_key = os.environ.get("METER_SOURCE_KEY", "hub2.prometheus")

    structure = parse_rules(rules_dir)
    if not structure:
        print(f"no rule files found under {rules_dir}", file=sys.stderr)
        return 1
    structure_hash = fingerprint(structure)
    pages = render_pages(source_key, structure, structure_hash)
    total_rules = sum(len(rules) for groups in structure.values() for rules in groups.values())
    print(f"fingerprint {structure_hash}")
    print(f"parsed {total_rules} rules from {len(structure)} files; rendered {len(pages)} pages")

    if args.dry_run:
        for page in pages:
            print(f"DRY-RUN would publish {page['path']} ({len(page['content'])} bytes)")
        return 0

    # Reuse the proven Sprint 5 machinery via the shared client: the
    # publish/declare/observe helpers live in schema_catalogue and operate on
    # module-level SECTION/PRESENCE constants, so the flow is inlined here
    # against this generator's own section and keys.
    import schema_catalogue as sc

    client = Client(os.environ["DOCPLANE_API"], os.environ["DOCPLANE_METER_LIST_TOKEN"])
    reconciled = reconcile_entities(
        client, source_key, structure, structure_hash,
        allow_mass_retirement=args.allow_mass_retirement,
    )
    source_id = reconciled["source_id"]
    print(
        "reconciled entities: "
        + ", ".join(f"{reconciled[key]} {key}" for key in ("created", "updated", "retired", "reactivated", "links_added", "links_removed"))
    )

    artifact_key = f"meter-list-{_slug(source_key)}"
    artifact = sc.current_artifact(client, artifact_key)
    if artifact is not None:
        previous = sc.last_generation_fingerprint(client, artifact["artifact_id"])
        if previous == structure_hash and not artifact_needs_succession(artifact, sorted(page["path"] for page in pages)):
            print(f"UNCHANGED {structure_hash[:16]} — nothing to regenerate")
            return 0

    def lookup(path: str) -> dict[str, Any] | None:
        found = client.call("GET", f"/api/v1/pages?path={path}").get("pages", [])
        return found[0] if found else None

    operations = []
    for page in pages:
        current = lookup(page["path"])
        if current is None:
            operations.append(("CREATE_PAGE", None, None, page))
        else:
            operations.append(("REPLACE_DOCUMENT", current["resource_id"], current["revision"], page))
    if lookup(PRESENCE_PATH) is None:
        operations.append(("CREATE_PAGE", None, None, presence_page()))
    # Pages the previous declaration owned but this rule set no longer
    # produces (a rule file removed from git) are archived in the same
    # governed change — "rule files are authoritative" includes absence.
    desired_paths = sorted(page["path"] for page in pages)
    stale_paths = sorted(set((artifact or {}).get("target_page_paths") or []) - set(desired_paths) - {PRESENCE_PATH})
    for path in stale_paths:
        current = lookup(path)
        if current is not None:
            operations.append(("ARCHIVE_PAGE", current["resource_id"], current["revision"], {"path": path}))

    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Meter list regeneration {structure_hash[:16]}",
            "purpose": f"Fingerprint-bound regeneration by the meter-list importer; rule-set fingerprint {structure_hash}.",
            "workspace_key": "reference",
        },
        _key(structure_hash, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        request: dict[str, Any] = {"operation_type": operation_type, "payload": {}}
        if operation_type != "ARCHIVE_PAGE":
            request["payload"] = {"path": page["path"], "title": page["title"], "nav_path": page["nav_path"], "content": page["content"]}
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call("POST", f"/api/v1/changes/{change_id}/operations", request, _key(structure_hash, "operation", page["path"]))
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(structure_hash, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(structure_hash, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")

    page_ids = {}
    for page in pages:
        found = lookup(page["path"])
        if found is None:
            raise RuntimeError(f"published page not found after publication: {page['path']}")
        page_ids[page["path"]] = found["resource_id"]

    if artifact is not None and artifact_needs_succession(artifact, sorted(page_ids)):
        # Retire-then-redeclare under the same key (migration 007's successor
        # path): retirement releases the old target set and its provenance
        # arming, the successor binds the current one. Both calls are
        # fingerprint-keyed, so a resumed run replays rather than double-acts.
        client.call(
            "POST", f"/api/v1/model/artifacts/{artifact['artifact_id']}/retire",
            {"expected_version": artifact["version"], "note": f"superseded by rule set {structure_hash[:16]}"},
            _key(structure_hash, "artifact-retire"),
        )
        print(f"RETIRED artifact {artifact['artifact_id']} — target set or generator contract moved")
        artifact = None
    if artifact is None:
        artifact = client.call(
            "POST", "/api/v1/model/artifacts",
            {
                "artifact_key": artifact_key,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "source_entity_id": source_id,
                "redaction_policy": "canonical",
                "target_page_resource_ids": sorted(page_ids.values()),
                "target_page_paths": sorted(page_ids),
            },
            _key(structure_hash, "artifact"),
        )
    client.call(
        "POST", "/api/v1/observations",
        {
            "observations": [
                {
                    "subject_artifact_id": artifact["artifact_id"],
                    "observation_kind": "GENERATION",
                    "outcome": "NOMINAL",
                    "source_fingerprint": structure_hash,
                    "summary": f"Imported {total_rules} monitoring rules from {len(structure)} files",
                    "idempotency_key": _key(structure_hash, "generation"),
                }
            ]
        },
        _key(structure_hash, "observation-batch"),
    )
    print(f"PUBLISHED {len(page_ids)} pages, artifact {artifact['artifact_id']}, fingerprint {structure_hash[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
