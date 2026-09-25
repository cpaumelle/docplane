#!/usr/bin/env python3
"""Invariants register generator — the know-domain register (DOMAIN_MODEL.md).

The invariants register is know-domain knowledge: standing rules that must hold
now, each with a stable id, a statement, a link to what established it, and the
load-bearing part — a pointer to what enforces it. Entries without one are
flagged for demotion ("an invariant without an enforcement pointer is a wish").

Its authoritative source is structured YAML in git, one file per domain; git
is the record history and audit trail. This generator renders that source as
ONE generated, ID-addressable register page and publishes it through the
governed change contract as a named AUTOMATION principal, following the
generated-artifact pattern of the schema catalogue, meter list and work
catalogue:

  know     one GENERATED register page (knowledge_class POLICY): a `##` section
           per domain and a `###` heading per invariant whose text is the bare
           id, so the site's own slug is the stable anchor (#i-foo-1)
  know     a permanent thin presence page, created only if absent
  model    one artifact declaration owning the register page, sourced from a
           SYSTEM card (the work-catalogue precedent) — provenance plumbing,
           not storage: invariant records are NOT model entities
  observe  a GENERATION observation carrying the source fingerprint

Environment:
  DOCPLANE_API                       routed front
  DOCPLANE_INVARIANT_REGISTER_TOKEN  AUTOMATION bearer (never logged); the _FILE
                                     variant is the supported delivery (SECRETS-V3)
  INVARIANT_SOURCE_DIR               directory of per-domain source files (<domain>.yaml)
  INVARIANT_REGISTER_PATH            register page path (default control-plane/invariants/register.md)
  INVARIANT_PRESENCE_PATH            presence page path (default control-plane/invariants/index.md)

Usage: invariant_register.py [--dry-run] [--validate-only] [--status-json] [--metrics-file PATH]
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

GENERATOR_NAME = "docplane-invariant-register"
GENERATOR_VERSION = "1.0.0"
PROJECTION_CONTRACT_VERSION = 1
SOURCE_SCHEMA_VERSION = 1
DEFAULT_REGISTER_PATH = "control-plane/invariants/register.md"
DEFAULT_PRESENCE_PATH = "control-plane/invariants/index.md"
ARTIFACT_KEY = "invariant-register"
SOURCE_ENTITY_KIND = "SYSTEM"
SOURCE_ENTITY_KEY = "invariant-registry"
KNOWLEDGE_CLASS = "POLICY"

LIFECYCLE = "REFERENCE"
LIFECYCLE_LINES = (f"**Lifecycle:** {LIFECYCLE}", f"<!-- lifecycle: {LIFECYCLE} -->")

# ── Source contract ─────────────────────────────────────────────────────────
# The minimum common schema. Vocabularies reuse DocPlane's page-trust terms
# (criticality, verification_state). Ratification and enforcement are separate
# on purpose: "ratified" and "enforced" are different facts.

ID_RE = re.compile(r"^I-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+$")
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAGE_PATH_RE = re.compile(r"^[a-z0-9/_-]+\.md$")  # the deployed publication path contract
RATIFICATION = ("PROPOSED", "RATIFIED", "SUPERSEDED")
ENFORCEMENT = ("DOCTRINE_ONLY", "PARTIAL", "ENFORCED")
CRITICALITY = ("NORMAL", "IMPORTANT", "OPERATIONAL_CRITICAL", "POLICY_REQUIRED")
VERIFICATION = ("UNVERIFIED", "VERIFIED", "OUTDATED", "EXPIRED")

REQUIRED = ("id", "title", "statement", "ratification", "enforcement", "criticality", "verification_state")
OPTIONAL_STR = ("owner", "verified_at", "verified_against", "review_due_at", "rationale",
                "established_at", "established_by", "origin", "specializes")
OPTIONAL_LIST = ("must_be_true", "supersedes", "aliases", "enforced_by", "enforcement_refs")
ALLOWED = set(REQUIRED) | set(OPTIONAL_STR) | set(OPTIONAL_LIST)
FILE_KEYS = {"schema_version", "domain", "owner", "title", "invariants"}


class SourceError(ValueError):
    """The source violates the contract. Carries every finding, not the first."""

    def __init__(self, findings: list[str]):
        super().__init__("\n".join(findings))
        self.findings = findings


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(_is_str(item) for item in value)


def has_enforcement_pointer(record: dict[str, Any]) -> bool:
    return bool(record.get("enforced_by") or record.get("enforcement_refs"))


def demotion_candidate(record: dict[str, Any]) -> bool:
    """The ratified register rule: an invariant without an enforcement pointer
    is a wish and is visibly flagged for demotion. Superseded records are
    history, not candidates."""
    return record["ratification"] != "SUPERSEDED" and (
        record["enforcement"] == "DOCTRINE_ONLY" or not has_enforcement_pointer(record)
    )


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
    for key in ("owner", "title"):
        if not _is_str(document.get(key)):
            findings.append(f"{where}: {key} is required")
    records = document.get("invariants")
    if not (isinstance(records, list) and records):
        findings.append(f"{where}: invariants must be a non-empty list")
        return findings
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            findings.append(f"{where}: invariants[{index}]: must be a mapping")
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
        if _is_str(record.get("established_by")) and not PAGE_PATH_RE.fullmatch(record["established_by"]):
            findings.append(f"{label}: established_by must be a DocPlane page path (e.g. operations/decisions/x.md)")
        if record.get("verification_state") == "VERIFIED":
            for key in ("verified_at", "verified_against"):
                if not _is_str(record.get(key)):
                    findings.append(f"{label}: {key} is required when verification_state is VERIFIED")
        # Claiming enforcement without saying what enforces it is the exact
        # failure the register exists to prevent.
        if record.get("enforcement") in ("PARTIAL", "ENFORCED") and not has_enforcement_pointer(record):
            findings.append(f"{label}: enforcement {record['enforcement']} requires enforced_by or enforcement_refs")
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


def iter_records(domains: dict[str, dict[str, Any]]):
    for domain in sorted(domains):
        for record in domains[domain]["invariants"]:
            yield domain, record


def decision_refs(domains: dict[str, dict[str, Any]]) -> list[str]:
    return sorted({record["established_by"] for _, record in iter_records(domains) if record.get("established_by")})


# ── Rendering ───────────────────────────────────────────────────────────────

def _brace_safe(text: str) -> str:
    """Prose may carry unbalanced braces, which the canonical redaction
    transform refuses; HTML entities render identically (meter-list lesson)."""
    return text.replace("{", "&#123;").replace("}", "&#125;")


def _cell(text: str) -> str:
    return _brace_safe(text.strip()).replace("|", "&#124;")


def _page_link(register_path: str, target: str) -> str:
    """Relative link from the register page to another DocPlane page."""
    return os.path.relpath(target, os.path.dirname(register_path) or ".")


def _record_section(record: dict[str, Any], document_owner: str, register_path: str) -> list[str]:
    # The heading text is the bare id: the site's own slug is the stable anchor
    # (#i-foo-1). Explicit {#id} attributes are not honoured by the site.
    lines = [f"### {record['id']}", "", f"**{_cell(record['title'])}**", ""]
    if demotion_candidate(record):
        lines += ["> **Demotion candidate** — no enforcement pointer. An invariant nothing enforces is a wish.", ""]
    verification = record["verification_state"]
    if record.get("verified_at"):
        verification += f" · {record['verified_at']}"
    if record.get("verified_against"):
        verification += f" · against {record['verified_against']}"
    facts = [
        ("Ratification", record["ratification"]),
        ("Enforcement", record["enforcement"]),
        ("Criticality", record["criticality"]),
        ("Verification", verification),
        ("Owner", record.get("owner") or document_owner),
    ]
    if record.get("established_by"):
        facts.append(("Established by", f"[{record['established_by']}]({_page_link(register_path, record['established_by'])})"))
    for key, label in (("established_at", "Established"), ("origin", "Origin"),
                       ("review_due_at", "Review due"), ("specializes", "Specializes")):
        if record.get(key):
            value = record[key]
            if key == "specializes":
                value = f"[{value}](#{value.lower()})"
            facts.append((label, value))
    for key, label in (("supersedes", "Supersedes"), ("aliases", "Also known as")):
        if record.get(key):
            facts.append((label, ", ".join(record[key])))
    lines += ["| | |", "|---|---|"]
    lines += [f"| {label} | {value if label in ('Established by', 'Specializes') else _cell(str(value))} |" for label, value in facts]
    lines += ["", "**Statement.** " + _brace_safe(record["statement"].strip()), ""]
    if record.get("must_be_true"):
        lines += ["**Must be true.**", ""] + [f"- {_brace_safe(item.strip())}" for item in record["must_be_true"]] + [""]
    if record.get("rationale"):
        lines += ["**Rationale.** " + _brace_safe(record["rationale"].strip()), ""]
    pointers = [f"`{name}`" for name in record.get("enforced_by", [])] + [
        _brace_safe(ref) for ref in record.get("enforcement_refs", [])
    ]
    if pointers:
        lines += ["**Enforced by.** " + " · ".join(pointers), ""]
    return lines


def render_register(domains: dict[str, dict[str, Any]], source_hash: str, register_path: str) -> dict[str, str]:
    stamp = (
        f"> Generated by `{GENERATOR_NAME}` {GENERATOR_VERSION} · source fingerprint "
        f"`{source_hash[:16]}` · the invariant sources in git are authoritative and hold "
        "the history. Edit them there, never here."
    )
    records = list(iter_records(domains))
    flagged = sum(1 for _, record in records if demotion_candidate(record))
    body = [
        "# Invariants register", "", *LIFECYCLE_LINES, "", stamp, "",
        f"{len(records)} invariants across {len(domains)} domains · {flagged} flagged for demotion. "
        "Each invariant is anchored by its lower-cased id (for example `#i-obs-liveness-1`).",
        "",
        "| Invariant | Domain | Title | Ratification | Enforcement |",
        "|---|---|---|---|---|",
    ]
    for domain, record in records:
        enforcement = record["enforcement"] + (" · demotion candidate" if demotion_candidate(record) else "")
        body.append(
            f"| [{record['id']}](#{record['id'].lower()}) | [{domain}](#{domain}) | {_cell(record['title'])} "
            f"| {record['ratification']} | {enforcement} |"
        )
    body.append("")
    for domain in sorted(domains):
        document = domains[domain]
        body += [f"## {domain}", "",
                 f"{_cell(document['title'])} · source `{document['source_file']}` · owner {document['owner']}", ""]
        for record in document["invariants"]:
            body += _record_section(record, document["owner"], register_path)
    content = "\n".join(body).rstrip() + "\n"
    if content.count("{") != content.count("}"):
        raise RuntimeError("unbalanced braces in the rendered register")
    return {
        "path": register_path,
        "title": "Invariants register",
        "nav_path": "Control Plane / Invariants / Register",
        "content": redact(content, label="invariant-register").sanitised,
    }


def presence_page(presence_path: str, register_path: str) -> dict[str, str]:
    """Permanent thin presence page (DOMAIN_MODEL.md): created only when absent,
    hand-curated afterwards, never regenerated."""
    return {
        "path": presence_path,
        "title": "Invariants",
        "nav_path": "Control Plane / Invariants / Overview",
        "content": (
            "# Invariants\n\n"
            f"**Lifecycle:** {LIFECYCLE}\n"
            f"<!-- lifecycle: {LIFECYCLE} -->\n\n"
            "DocPlane tracks the invariants register. The register is generated from "
            f"per-domain invariant sources in git by the `{GENERATOR_NAME}` AUTOMATION "
            "principal and republishes only when the source fingerprint changes: "
            f"[Invariants register]({_page_link(presence_path, register_path)}).\n\n"
            "This presence page is permanent and hand-curated: it survives every "
            "regeneration and is the stable place for ownership notes.\n"
        ),
    }


def _key(source_hash: str, verb: str, discriminator: str = "") -> str:
    return (
        f"invariant-register-{GENERATOR_VERSION}-pc{PROJECTION_CONTRACT_VERSION}-{source_hash[:16]}-{verb}"
        f"{'-' + discriminator if discriminator else ''}"
    )[:256]


def artifact_needs_reconciliation(artifact: dict[str, Any], desired_paths: list[str]) -> bool:
    return (
        artifact.get("projection_contract_version", 1) != PROJECTION_CONTRACT_VERSION
        or sorted(artifact.get("target_page_paths") or []) != sorted(desired_paths)
        or artifact.get("generator_version") != GENERATOR_VERSION
    )


def lookup(client: Client, path: str) -> dict[str, Any] | None:
    found = client.call("GET", f"/api/v1/pages?path={path}&status=all").get("pages", [])
    return found[0] if found else None


def check_target_collision(client: Client, register_path: str, artifact: dict[str, Any] | None) -> None:
    """A register path already holding a page this artifact does not own (a
    hand-authored page) must never be replaced and silently adopted."""
    if register_path in set((artifact or {}).get("target_page_paths") or []):
        return
    current = lookup(client, register_path)
    if current is not None:
        raise RuntimeError(
            f"{register_path} already exists (resource {current['resource_id']}, not owned by "
            f"{ARTIFACT_KEY}) — refusing to replace it with the generated register"
        )


def check_decision_refs(client: Client, domains: dict[str, dict[str, Any]]) -> None:
    """Every established_by must name a real page (active or archived — an
    archived decision or incident is still the record). Fail closed rather than
    publish a register that links nowhere."""
    missing = [path for path in decision_refs(domains) if lookup(client, path) is None]
    if missing:
        raise RuntimeError(f"established_by names pages that do not exist: {', '.join(missing)}")


def ensure_source_entity(client: Client, source_hash: str) -> str:
    listing = client.call("GET", f"/api/v1/model/entities?entity_kind={SOURCE_ENTITY_KIND}&limit=1000")
    for entity in listing.get("entities", []):
        if entity["entity_key"] == SOURCE_ENTITY_KEY:
            return entity["entity_id"]
    return client.call(
        "POST", "/api/v1/model/entities",
        {"entity_kind": SOURCE_ENTITY_KIND, "entity_key": SOURCE_ENTITY_KEY,
         "display_name": "Invariant registry (git)",
         "attributes": {"description": "Source of the generated invariants register"}},
        _key(source_hash, "source-entity"),
    )["entity_id"]


# ── The run ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true", help="validate the source and exit; no API access")
    parser.add_argument("--status-json", action="store_true", help="print projection status as JSON; perform no writes")
    parser.add_argument("--metrics-file", help="atomically write projection status in Prometheus textfile format")
    parser.add_argument("--reconcile-success", choices=("0", "1"), default="1", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    source_dir = Path(os.environ["INVARIANT_SOURCE_DIR"])
    register_path = os.environ.get("INVARIANT_REGISTER_PATH", DEFAULT_REGISTER_PATH).strip("/")
    presence_path = os.environ.get("INVARIANT_PRESENCE_PATH", DEFAULT_PRESENCE_PATH).strip("/")
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
    register = render_register(domains, source_hash, register_path)
    total = sum(len(document["invariants"]) for document in domains.values())
    flagged = sum(1 for _, record in iter_records(domains) if demotion_candidate(record))

    if args.validate_only:
        print(f"VALID {total} invariants across {len(domains)} domains, {flagged} flagged for demotion, fingerprint {source_hash[:16]}")
        return 0
    if args.dry_run:
        print(f"fingerprint {source_hash}")
        print(f"parsed {total} invariants from {len(domains)} domains; {flagged} flagged for demotion")
        print(f"DRY-RUN would publish {register['path']} ({len(register['content'])} bytes)")
        return 0

    import schema_catalogue as sc

    client = Client(os.environ["DOCPLANE_API"], read_secret("DOCPLANE_INVARIANT_REGISTER_TOKEN"))
    desired_paths = [register_path]

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
    previous = sc.last_generation_fingerprint(client, artifact["artifact_id"]) if artifact else None

    def observe_generation(current_artifact: dict[str, Any]) -> None:
        client.call(
            "POST", "/api/v1/observations",
            {"observations": [{
                "subject_artifact_id": current_artifact["artifact_id"],
                "observation_kind": "GENERATION",
                "outcome": "NOMINAL",
                "source_fingerprint": source_hash,
                "summary": f"Rendered {total} invariants from {len(domains)} domain sources ({flagged} flagged for demotion)",
                "idempotency_key": _key(source_hash, "generation"),
            }]},
            _key(source_hash, "observation-batch"),
        )

    if artifact is not None and previous == source_hash and not artifact_needs_reconciliation(artifact, desired_paths):
        observe_generation(artifact)
        print(f"UNCHANGED {source_hash[:16]} — nothing to regenerate")
        return 0

    check_target_collision(client, register_path, artifact)
    check_decision_refs(client, domains)

    operations = []
    current = lookup(client, register_path)
    if current is None:
        register_id = str(uuid4())
        operations.append(("CREATE_PAGE", None, None, {**register, "resource_id": register_id}))
    else:
        register_id = current["resource_id"]
        if current.get("status") == "archived":
            operations.append(("RESTORE_PAGE", register_id, current["revision"], register))
        operations.append(("REPLACE_DOCUMENT", register_id, current["revision"], register))
    if lookup(client, presence_path) is None:
        presence = presence_page(presence_path, register_path)
        operations.append(("CREATE_PAGE", None, None, {**presence, "resource_id": str(uuid4())}))

    if artifact is None:
        artifact = client.call(
            "POST", "/api/v1/model/artifacts",
            {
                "artifact_key": ARTIFACT_KEY,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "projection_contract_version": PROJECTION_CONTRACT_VERSION,
                "source_entity_id": ensure_source_entity(client, source_hash),
                "redaction_policy": "canonical",
                "target_page_resource_ids": [],
                "target_page_paths": [],
            },
            _key(source_hash, "artifact-empty"),
        )

    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Invariants register regeneration {source_hash[:16]}",
            "purpose": f"Fingerprint-bound regeneration by the invariant-register generator; source fingerprint {source_hash}.",
            "workspace_key": "reference",
            "generated_ownership_plan": {
                "mode": "IN_PLACE",
                "artifact_id": artifact["artifact_id"],
                "expected_version": artifact["version"],
                "target_page_resource_ids": [register_id],
                "target_page_paths": desired_paths,
                "generator_version": GENERATOR_VERSION,
            },
        },
        _key(source_hash, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        request: dict[str, Any] = {"operation_type": operation_type, "payload": {}}
        if operation_type == "CREATE_PAGE":
            request["payload"] = {"path": page["path"], "title": page["title"], "nav_path": page["nav_path"],
                                  "content": page["content"], "resource_id": page["resource_id"]}
            if page["path"] == register_path:
                request["payload"]["knowledge_class"] = KNOWLEDGE_CLASS
        elif operation_type == "REPLACE_DOCUMENT":
            request["payload"] = {"title": page["title"], "nav_path": page["nav_path"], "content": page["content"]}
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
    observe_generation(artifact)
    print(f"PUBLISHED {register_path}, artifact {artifact['artifact_id']}, fingerprint {source_hash[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
