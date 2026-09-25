#!/usr/bin/env python3
"""Invariant catalogue importer — invariants as model records, pages as views.

Invariants are governed records. Their authoritative source is structured YAML
in git (one file per domain), reviewed and versioned there; git is the history
and audit trail. This importer drives the DocPlane API as a named AUTOMATION
principal and projects that source, following the meter-list exemplar:

  model    one INVARIANT entity per record (current state only), keyed by the
           lower-cased invariant id, so docplane://model/invariant/<id> is a
           stable identity independent of any page path
  know     one generated catalogue page per domain, with one heading per
           invariant whose text is the bare id — the rendered anchor is
           therefore #<lower-cased id>, stable across title edits
  model    one artifact declaration owning the generated pages
  model    exact INVARIANT -> domain page CATALOGUES links after publication
  observe  a GENERATION observation carrying the source fingerprint

The importer owns the INVARIANT kind outright: an id absent from every source
file is retired (bounded by a mass-retirement guard); an id restored in git is
reactivated, never duplicated.

Environment:
  DOCPLANE_API                        routed front
  DOCPLANE_INVARIANT_CATALOGUE_TOKEN  AUTOMATION bearer (never logged); the
                                      _FILE variant is preferred (SECRETS-V3)
  INVARIANT_SOURCE_DIR                directory of per-domain source files
                                      (<domain>.yaml), e.g. a git checkout's
                                      invariants/ directory
  INVARIANT_SECTION                   page section for the generated views
                                      (default control-plane/invariants)

Usage: invariant_catalogue.py [--dry-run] [--validate-only] [--allow-mass-retirement]
                              [--status-json] [--metrics-file PATH]
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
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from migration.redaction import redact  # noqa: E402
from schema_catalogue import Client  # noqa: E402  (shared API client)
from secret_source import read_secret  # noqa: E402  (SECRETS-V3 `_FILE` contract)

GENERATOR_NAME = "docplane-invariant-catalogue"
GENERATOR_VERSION = "1.0.0"
PROJECTION_CONTRACT_VERSION = 1
SOURCE_SCHEMA_VERSION = 1
DEFAULT_SECTION = "control-plane/invariants"
ARTIFACT_KEY = "invariant-catalogue"
SOURCE_ENTITY_KIND = "SYSTEM"
SOURCE_ENTITY_KEY = "invariant-registry"

LIFECYCLE = "REFERENCE"
LIFECYCLE_LINES = (f"**Lifecycle:** {LIFECYCLE}", f"<!-- lifecycle: {LIFECYCLE} -->")

# ── Source contract ─────────────────────────────────────────────────────────
# The minimum common schema. Vocabularies reuse DocPlane's own page-trust
# terms (criticality, verification_state) so a record and a page speak the
# same language. Ratification and enforcement are separate on purpose: a
# ratified invariant is not necessarily enforced, and a single status field
# is exactly how "ENFORCED ... implementation pending" contradictions arise.

ID_RE = re.compile(r"^I-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+$")
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T[0-9:.]+Z)?$")
RATIFICATION = ("PROPOSED", "RATIFIED", "SUPERSEDED")
ENFORCEMENT = ("DOCTRINE_ONLY", "PARTIAL", "ENFORCED")
CRITICALITY = ("NORMAL", "IMPORTANT", "OPERATIONAL_CRITICAL", "POLICY_REQUIRED")
VERIFICATION = ("UNVERIFIED", "VERIFIED", "OUTDATED", "EXPIRED")

REQUIRED = ("id", "title", "statement", "ratification", "enforcement", "criticality", "verification_state")
OPTIONAL_STR = ("owner", "verified_at", "verified_against", "review_due_at", "rationale", "established_at", "origin", "specializes")
OPTIONAL_LIST = ("must_be_true", "supersedes", "aliases", "enforced_by", "enforcement_refs")
ALLOWED = set(REQUIRED) | set(OPTIONAL_STR) | set(OPTIONAL_LIST)
FILE_KEYS = {"schema_version", "domain", "owner", "view", "invariants"}


class SourceError(ValueError):
    """The source violates the contract. Carries every finding, not the first."""

    def __init__(self, findings: list[str]):
        super().__init__("\n".join(findings))
        self.findings = findings


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(_is_str(item) for item in value)


def validate_document(document: Any, filename: str) -> list[str]:
    """Contract findings for one domain file; empty means valid."""
    where = filename
    if not isinstance(document, dict):
        return [f"{where}: top level must be a mapping"]
    findings: list[str] = []
    for key in sorted(set(document) - FILE_KEYS):
        findings.append(f"{where}: unknown top-level key {key!r}")
    if document.get("schema_version") != SOURCE_SCHEMA_VERSION:
        findings.append(f"{where}: schema_version must be {SOURCE_SCHEMA_VERSION}")
    domain = document.get("domain")
    if not (isinstance(domain, str) and DOMAIN_RE.fullmatch(domain)):
        findings.append(f"{where}: domain must match {DOMAIN_RE.pattern}")
    elif Path(filename).stem != domain:
        findings.append(f"{where}: file name must be <domain>.yaml ({domain}.yaml)")
    if not _is_str(document.get("owner")):
        findings.append(f"{where}: owner is required")
    view = document.get("view")
    if not (isinstance(view, dict) and _is_str(view.get("title"))):
        findings.append(f"{where}: view.title is required")
    records = document.get("invariants")
    if not (isinstance(records, list) and records):
        findings.append(f"{where}: invariants must be a non-empty list")
        return findings
    seen: set[str] = set()
    for index, record in enumerate(records):
        label = f"{where}: invariants[{index}]"
        if not isinstance(record, dict):
            findings.append(f"{label}: must be a mapping")
            continue
        label = f"{where}: {record.get('id', f'invariants[{index}]')}"
        for key in sorted(set(record) - ALLOWED):
            findings.append(f"{label}: unknown field {key!r}")
        for key in REQUIRED:
            if not _is_str(record.get(key)):
                findings.append(f"{label}: {key} is required")
        if _is_str(record.get("id")):
            if not ID_RE.fullmatch(record["id"]):
                findings.append(f"{label}: id must match {ID_RE.pattern}")
            if record["id"] in seen:
                findings.append(f"{label}: duplicate id in file")
            seen.add(record["id"])
        for key, vocabulary in (
            ("ratification", RATIFICATION), ("enforcement", ENFORCEMENT),
            ("criticality", CRITICALITY), ("verification_state", VERIFICATION),
        ):
            if _is_str(record.get(key)) and record[key] not in vocabulary:
                findings.append(f"{label}: {key} must be one of {', '.join(vocabulary)}")
        for key in OPTIONAL_STR:
            if key in record and not _is_str(record[key]):
                findings.append(f"{label}: {key} must be a non-empty string")
        for key in OPTIONAL_LIST:
            if key in record and not _is_str_list(record[key]):
                findings.append(f"{label}: {key} must be a non-empty list of strings")
        for key in ("verified_at", "review_due_at", "established_at"):
            if _is_str(record.get(key)) and not DATE_RE.fullmatch(record[key]):
                findings.append(f"{label}: {key} must be an ISO date (YYYY-MM-DD)")
        if record.get("verification_state") == "VERIFIED":
            for key in ("verified_at", "verified_against"):
                if not _is_str(record.get(key)):
                    findings.append(f"{label}: {key} is required when verification_state is VERIFIED")
    return findings


def load_sources(source_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse and validate every domain file; fail closed on any finding,
    including an id declared in two domains (identity is corpus-wide)."""
    files = sorted(source_dir.glob("*.yaml")) + sorted(source_dir.glob("*.yml"))
    findings: list[str] = []
    domains: dict[str, dict[str, Any]] = {}
    owners_of_id: dict[str, str] = {}
    for path in files:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            findings.append(f"{path.name}: not valid YAML ({exc.__class__.__name__})")
            continue
        file_findings = validate_document(document, path.name)
        findings += file_findings
        if file_findings:
            continue
        domain = document["domain"]
        if domain in domains:
            findings.append(f"{path.name}: domain {domain!r} declared by more than one file")
            continue
        for record in document["invariants"]:
            if record["id"] in owners_of_id:
                findings.append(f"{path.name}: {record['id']} already declared in {owners_of_id[record['id']]}")
            owners_of_id[record["id"]] = path.name
        domains[domain] = {**document, "source_file": path.name}
    if findings:
        raise SourceError(findings)
    return domains


def fingerprint(domains: dict[str, dict[str, Any]]) -> str:
    canonical = json.dumps(domains, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{GENERATOR_VERSION}\n{canonical}".encode("utf-8")).hexdigest()


def entity_key(invariant_id: str) -> str:
    return invariant_id.lower()


def page_path(section: str, domain: str) -> str:
    return f"{section.strip('/')}/{domain}.md"


def iter_records(domains: dict[str, dict[str, Any]]):
    for domain in sorted(domains):
        for record in domains[domain]["invariants"]:
            yield domain, record


def record_attributes(domain: str, document: dict[str, Any], record: dict[str, Any], section: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "invariant_id": record["id"],
        "domain": domain,
        "owner": record.get("owner") or document["owner"],
        "statement": record["statement"].strip(),
        "ratification": record["ratification"],
        "enforcement": record["enforcement"],
        "criticality": record["criticality"],
        "verification_state": record["verification_state"],
        "source_file": document["source_file"],
        "source_page_path": page_path(section, domain),
    }
    for key in OPTIONAL_STR:
        if key != "owner" and record.get(key):
            attributes[key] = record[key].strip()
    for key in OPTIONAL_LIST:
        if record.get(key):
            attributes[key] = [item.strip() for item in record[key]]
    return attributes


# ── Rendering ───────────────────────────────────────────────────────────────

def _brace_safe(text: str) -> str:
    """Prose may carry unbalanced braces, which the canonical redaction
    transform refuses; HTML entities render identically (meter-list lesson)."""
    return text.replace("{", "&#123;").replace("}", "&#125;")


def _record_section(record: dict[str, Any], document_owner: str) -> list[str]:
    # The heading text is the bare id, so the renderer's own slug is the
    # stable anchor (#i-foo-1). Explicit {#id} attributes are deliberately
    # not used: the site's markdown configuration does not honour them.
    lines = [f"### {record['id']}", "", f"**{_brace_safe(record['title'].strip())}**", ""]
    facts = [
        ("Ratification", record["ratification"]),
        ("Enforcement", record["enforcement"]),
        ("Criticality", record["criticality"]),
        ("Verification", record["verification_state"]
            + (f" · {record['verified_at']}" if record.get("verified_at") else "")
            + (f" · against {record['verified_against']}" if record.get("verified_against") else "")),
        ("Owner", record.get("owner") or document_owner),
    ]
    for key, label in (("review_due_at", "Review due"), ("established_at", "Established"),
                       ("origin", "Origin"), ("specializes", "Specializes")):
        if record.get(key):
            facts.append((label, record[key]))
    for key, label in (("supersedes", "Supersedes"), ("aliases", "Also known as")):
        if record.get(key):
            facts.append((label, ", ".join(record[key])))
    lines += ["| | |", "|---|---|"]
    lines += [f"| {label} | {_brace_safe(str(value)).replace('|', '&#124;')} |" for label, value in facts]
    lines += ["", "**Statement.** " + _brace_safe(record["statement"].strip()), ""]
    if record.get("must_be_true"):
        lines += ["**Must be true.**", ""] + [f"- {_brace_safe(item.strip())}" for item in record["must_be_true"]] + [""]
    if record.get("rationale"):
        lines += ["**Rationale.** " + _brace_safe(record["rationale"].strip()), ""]
    enforced = [f"`{name}`" for name in record.get("enforced_by", [])] + [
        _brace_safe(ref) for ref in record.get("enforcement_refs", [])
    ]
    if enforced:
        lines += ["**Enforced by.** " + " · ".join(enforced), ""]
    return lines


def render_pages(domains: dict[str, dict[str, Any]], source_hash: str, section: str) -> list[dict[str, str]]:
    stamp = (
        f"> Generated by `{GENERATOR_NAME}` {GENERATOR_VERSION} · source fingerprint "
        f"`{source_hash[:16]}` · the invariant source in git is authoritative and holds "
        "the history. Edit it there, never here."
    )
    pages = []
    for domain in sorted(domains):
        document = domains[domain]
        view = document["view"]
        records = document["invariants"]
        body = [f"# {view['title'].strip()}", "", *LIFECYCLE_LINES, "", stamp, "",
                f"Source: `{document['source_file']}` · {len(records)} invariants · owner {document['owner']}", ""]
        body += ["| Invariant | Title | Ratification | Enforcement | Verification |", "|---|---|---|---|---|"]
        for record in records:
            body.append(
                f"| [{record['id']}](#{record['id'].lower()}) | {_brace_safe(record['title'].strip()).replace('|', '&#124;')} "
                f"| {record['ratification']} | {record['enforcement']} | {record['verification_state']} |"
            )
        body.append("")
        for record in records:
            body += _record_section(record, document["owner"])
        content = "\n".join(body).rstrip() + "\n"
        if content.count("{") != content.count("}"):
            raise RuntimeError(f"unbalanced braces in rendered view for {domain}")
        pages.append({
            "path": page_path(section, domain),
            "title": view["title"].strip(),
            "nav_path": view.get("nav_path") or f"Control Plane / Invariants / {view['title'].strip()}",
            "content": redact(content, label="invariant-catalogue").sanitised,
        })
    return pages


def _key(source_hash: str, verb: str, discriminator: str = "") -> str:
    return (
        f"invariant-catalogue-{GENERATOR_VERSION}-pc{PROJECTION_CONTRACT_VERSION}-{source_hash[:16]}-{verb}"
        f"{'-' + discriminator if discriminator else ''}"
    )[:256]


# ── Model reconciliation ────────────────────────────────────────────────────

MASS_RETIREMENT_FLOOR = 3
MASS_RETIREMENT_SHARE = 0.2


def reconcile_entities(
    client: Client,
    domains: dict[str, dict[str, Any]],
    source_hash: str,
    section: str,
    *,
    allow_mass_retirement: bool = False,
) -> dict[str, Any]:
    """create / update / retire / reactivate, exactly the meter-list verbs.
    The INVARIANT kind is owned outright by this importer, so the retirement
    bound is the kind itself — nothing outside it is ever touched."""
    def list_all(query: str) -> list[dict[str, Any]]:
        listing = client.call("GET", f"/api/v1/model/entities?{query}&limit=1000")
        if listing.get("truncated"):
            raise RuntimeError(f"entity listing truncated for {query} — refusing to reconcile against a partial model")
        return listing.get("entities", [])

    systems = {entity["entity_key"]: entity for entity in list_all(f"entity_kind={SOURCE_ENTITY_KIND}")}
    if SOURCE_ENTITY_KEY in systems:
        source_id = systems[SOURCE_ENTITY_KEY]["entity_id"]
    else:
        source_id = client.call(
            "POST", "/api/v1/model/entities",
            {"entity_kind": SOURCE_ENTITY_KIND, "entity_key": SOURCE_ENTITY_KEY,
             "display_name": "Invariant registry (git)",
             "attributes": {"description": "Source of the generated invariant catalogue"}},
            _key(source_hash, "source-entity"),
        )["entity_id"]

    existing = {entity["entity_key"]: entity for entity in list_all("entity_kind=INVARIANT&status=all")}
    summary = {"created": 0, "updated": 0, "retired": 0, "reactivated": 0}
    desired: dict[str, dict[str, Any]] = {}
    for domain, record in iter_records(domains):
        desired[entity_key(record["id"])] = {
            "display_name": f"{record['id']} — {record['title'].strip()}"[:300],
            "attributes": record_attributes(domain, domains[domain], record, section),
        }

    active_count = sum(1 for entity in existing.values() if entity["status"] == "ACTIVE")
    to_retire = [key for key in sorted(existing) if key not in desired and existing[key]["status"] == "ACTIVE"]
    threshold = max(MASS_RETIREMENT_FLOOR, int(active_count * MASS_RETIREMENT_SHARE))
    if len(to_retire) > threshold and not allow_mass_retirement:
        raise RuntimeError(
            f"refusing to retire {len(to_retire)} of {active_count} invariants (bound {threshold}) — "
            "verify the source checkout, then rerun with --allow-mass-retirement"
        )
    for key in to_retire:
        entity = existing[key]
        client.call(
            "POST", f"/api/v1/model/entities/{entity['entity_id']}/retire",
            {"expected_version": entity["version"], "note": f"absent from invariant source {source_hash[:16]}"},
            _key(source_hash, "retire", key),
        )
        summary["retired"] += 1
        print(f"RETIRED {key} — no longer in any invariant source file")

    for key in sorted(desired):
        wanted = desired[key]
        entity = existing.get(key)
        if entity is not None and entity["status"] == "RETIRED":
            entity = existing[key] = client.call(
                "POST", f"/api/v1/model/entities/{entity['entity_id']}/reactivate",
                {"expected_version": entity["version"], "note": f"restored in invariant source {source_hash[:16]}"},
                _key(source_hash, "reactivate", key),
            )
            summary["reactivated"] += 1
        if entity is None:
            existing[key] = client.call(
                "POST", "/api/v1/model/entities",
                {"entity_kind": "INVARIANT", "entity_key": key,
                 "display_name": wanted["display_name"], "attributes": wanted["attributes"]},
                _key(source_hash, "entity", key),
            )
            summary["created"] += 1
        elif entity["display_name"] != wanted["display_name"] or entity.get("attributes") != wanted["attributes"]:
            existing[key] = client.call(
                "POST", f"/api/v1/model/entities/{entity['entity_id']}/update",
                {"expected_version": entity["version"], "display_name": wanted["display_name"],
                 "attributes": wanted["attributes"]},
                _key(source_hash, "update", key),
            )
            summary["updated"] += 1

    return {
        "source_id": source_id,
        "active_pages": {existing[key]["entity_id"]: wanted["attributes"]["source_page_path"] for key, wanted in sorted(desired.items())},
        "retired_ids": sorted(entity["entity_id"] for key, entity in existing.items() if key not in desired),
        **summary,
    }


def catalogues_mappings(reconciled: dict[str, Any], page_ids: dict[str, str]) -> dict[str, list[str]]:
    mappings = {entity_id: [page_ids[path]] for entity_id, path in sorted(reconciled["active_pages"].items())}
    for entity_id in reconciled["retired_ids"]:
        mappings[entity_id] = []
    return mappings


def artifact_needs_reconciliation(artifact: dict[str, Any], desired_paths: list[str]) -> bool:
    return (
        artifact.get("projection_contract_version", 1) != PROJECTION_CONTRACT_VERSION
        or sorted(artifact.get("target_page_paths") or []) != sorted(desired_paths)
        or artifact.get("generator_version") != GENERATOR_VERSION
    )


def check_target_collisions(client: Client, pages: list[dict[str, str]], artifact: dict[str, Any] | None) -> None:
    """A view path that already holds a page this artifact does not own
    (a hand-authored page) must never be replaced and silently adopted as
    GENERATED. Refuse and name it; the operator resolves ownership."""
    owned = set((artifact or {}).get("target_page_paths") or [])
    for page in pages:
        if page["path"] in owned:
            continue
        found = client.call("GET", f"/api/v1/pages?path={page['path']}&status=all").get("pages", [])
        if found:
            raise RuntimeError(
                f"{page['path']} already exists (resource {found[0]['resource_id']}, not owned by "
                f"{ARTIFACT_KEY}) — refusing to replace it with a generated view"
            )


# ── The run ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true", help="validate the source and exit; no API access")
    parser.add_argument("--allow-mass-retirement", action="store_true")
    parser.add_argument("--status-json", action="store_true", help="print projection status as JSON; perform no writes")
    parser.add_argument("--metrics-file", help="atomically write projection status in Prometheus textfile format")
    parser.add_argument("--reconcile-success", choices=("0", "1"), default="1", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    source_dir = Path(os.environ["INVARIANT_SOURCE_DIR"])
    section = os.environ.get("INVARIANT_SECTION", DEFAULT_SECTION).strip("/")
    try:
        domains = load_sources(source_dir)
    except SourceError as exc:
        for finding in exc.findings:
            print(f"INVALID {finding}", file=sys.stderr)
        return 1
    if not domains:
        print(f"no invariant source files under {source_dir}", file=sys.stderr)
        return 1
    source_hash = fingerprint(domains)
    pages = render_pages(domains, source_hash, section)
    total = sum(len(document["invariants"]) for document in domains.values())

    if args.validate_only:
        print(f"VALID {total} invariants across {len(domains)} domains, fingerprint {source_hash[:16]}")
        return 0
    if args.dry_run:
        print(f"fingerprint {source_hash}")
        print(f"parsed {total} invariants from {len(domains)} domains; rendered {len(pages)} pages")
        for page in pages:
            print(f"DRY-RUN would publish {page['path']} ({len(page['content'])} bytes)")
        return 0

    import schema_catalogue as sc

    client = Client(os.environ["DOCPLANE_API"], read_secret("DOCPLANE_INVARIANT_CATALOGUE_TOKEN"))
    desired_paths = sorted(page["path"] for page in pages)

    if args.status_json or args.metrics_file:
        artifact = sc.current_artifact(client, ARTIFACT_KEY)
        published = sc.last_generation_fingerprint(client, artifact["artifact_id"]) if artifact else None
        drift = artifact is None or published != source_hash or artifact_needs_reconciliation(artifact, desired_paths)
        if args.metrics_file:
            sc.write_projection_metrics(args.metrics_file, artifact=ARTIFACT_KEY, drift=drift, success=args.reconcile_success == "1")
        if args.status_json:
            print(json.dumps({
                "artifact_key": ARTIFACT_KEY,
                "artifact_id": artifact.get("artifact_id") if artifact else None,
                "live_fingerprint": source_hash,
                "published_fingerprint": published,
                "drift": drift,
                "reconcile_success": args.reconcile_success == "1",
            }, sort_keys=True))
        return 0

    print(f"fingerprint {source_hash}")
    artifact = sc.current_artifact(client, ARTIFACT_KEY)
    check_target_collisions(client, pages, artifact)
    reconciled = reconcile_entities(client, domains, source_hash, section, allow_mass_retirement=args.allow_mass_retirement)
    print(
        f"parsed {total} invariants from {len(domains)} domains; reconciled entities: "
        + ", ".join(f"{reconciled[key]} {key}" for key in ("created", "updated", "retired", "reactivated"))
    )
    previous = sc.last_generation_fingerprint(client, artifact["artifact_id"]) if artifact else None

    def observe_generation(current_artifact: dict[str, Any]) -> None:
        client.call(
            "POST", "/api/v1/observations",
            {"observations": [{
                "subject_artifact_id": current_artifact["artifact_id"],
                "observation_kind": "GENERATION",
                "outcome": "NOMINAL",
                "source_fingerprint": source_hash,
                "summary": f"Imported {total} invariants from {len(domains)} domain sources",
                "idempotency_key": _key(source_hash, "generation"),
            }]},
            _key(source_hash, "observation-batch"),
        )

    if artifact is not None and previous == source_hash and not artifact_needs_reconciliation(artifact, desired_paths):
        page_ids = sc.page_ids_for_paths(client, desired_paths)
        sc.reconcile_catalogues(client, catalogues_mappings(reconciled, page_ids), key_prefix=_key(source_hash, "semantic"))
        observe_generation(artifact)
        print(f"UNCHANGED {source_hash[:16]} — nothing to regenerate")
        return 0

    def lookup(path: str) -> dict[str, Any] | None:
        found = client.call("GET", f"/api/v1/pages?path={path}&status=all").get("pages", [])
        return found[0] if found else None

    operations = []
    page_ids: dict[str, str] = {}
    for page in pages:
        current = lookup(page["path"])
        if current is None:
            resource_id = str(uuid4())
            page_ids[page["path"]] = resource_id
            operations.append(("CREATE_PAGE", None, None, {**page, "resource_id": resource_id}))
        else:
            page_ids[page["path"]] = current["resource_id"]
            if current.get("status") == "archived":
                operations.append(("RESTORE_PAGE", current["resource_id"], current["revision"], page))
            operations.append(("REPLACE_DOCUMENT", current["resource_id"], current["revision"], page))
    # A domain file removed from git takes its view with it, in the same change.
    for path in sorted(set((artifact or {}).get("target_page_paths") or []) - set(desired_paths)):
        current = lookup(path)
        if current is not None and current.get("status") != "archived":
            operations.append(("ARCHIVE_PAGE", current["resource_id"], current["revision"], {"path": path}))

    if artifact is None:
        artifact = client.call(
            "POST", "/api/v1/model/artifacts",
            {
                "artifact_key": ARTIFACT_KEY,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "projection_contract_version": PROJECTION_CONTRACT_VERSION,
                "source_entity_id": reconciled["source_id"],
                "redaction_policy": "canonical",
                "target_page_resource_ids": [],
                "target_page_paths": [],
            },
            _key(source_hash, "artifact-empty"),
        )

    ownership_plan = {
        "mode": "IN_PLACE",
        "artifact_id": artifact["artifact_id"],
        "expected_version": artifact["version"],
        "target_page_resource_ids": [page_ids[path] for path in desired_paths],
        "target_page_paths": desired_paths,
        "generator_version": GENERATOR_VERSION,
    }
    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Invariant catalogue regeneration {source_hash[:16]}",
            "purpose": f"Fingerprint-bound regeneration by the invariant-catalogue importer; source fingerprint {source_hash}.",
            "workspace_key": "reference",
            "generated_ownership_plan": ownership_plan,
        },
        _key(source_hash, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        request: dict[str, Any] = {"operation_type": operation_type, "payload": {}}
        if operation_type in {"CREATE_PAGE", "REPLACE_DOCUMENT"}:
            request["payload"] = {"path": page["path"], "title": page["title"], "nav_path": page["nav_path"], "content": page["content"]}
            if operation_type == "CREATE_PAGE":
                request["payload"]["resource_id"] = page["resource_id"]
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call("POST", f"/api/v1/changes/{change_id}/operations", request,
                    _key(source_hash, "operation", f"{operation_type}:{page['path']}"))
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(source_hash, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(source_hash, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")

    artifact = sc.current_artifact(client, ARTIFACT_KEY)
    if artifact is None:
        raise RuntimeError("publication committed without an active generated-artifact owner")
    sc.reconcile_catalogues(client, catalogues_mappings(reconciled, page_ids), key_prefix=_key(source_hash, "semantic"))
    observe_generation(artifact)
    print(f"PUBLISHED {len(page_ids)} pages, artifact {artifact['artifact_id']}, fingerprint {source_hash[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
