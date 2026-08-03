#!/usr/bin/env python3
"""Work-catalogue generator — the third pass through the generated-artifact
machinery (after the schema catalogue and the meter list).

The work domain lives in ``work.*`` tables and is *acted on* in the dashboard;
this generator gives it a browsable, read-only surface on the published site:
a ``work/`` section whose pages are rendered from live initiative state so a
reader can survey the roadmap, check on soaks, or pick the next sprint
candidate without opening the dashboard. The site never becomes a second
write path — every page links back to the dashboard for action.

Contract, identical to the sibling generators:

  - deterministic rendering (no wall clock in content) over a projection of
    the work API; the sha256 of that rendering is the source fingerprint;
  - an UNCHANGED fingerprint publishes nothing and replays one NOMINAL
    GENERATION observation;
  - a changed fingerprint publishes one governed change (CREATE_PAGE /
    REPLACE_DOCUMENT, plus ARCHIVE_PAGE for pages the previous declaration
    owned that are no longer produced), then retires/redeclares the artifact
    when its target set or generator contract moved;
  - inbox captures are surfaced as a COUNT only. Pre-triage thoughts are not
    documentation and never appear on the site.

Environment:
  DOCPLANE_API                  routed front
  DOCPLANE_WORK_CATALOGUE_TOKEN named AUTOMATION bearer (never logged)

Usage: work_catalogue.py [--dry-run] [--status-json] [--metrics-file PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schema_catalogue import Client  # noqa: E402  (shared API client)
import schema_catalogue as sc  # noqa: E402

GENERATOR_NAME = "work-catalogue"
GENERATOR_VERSION = "1.0.0"
ARTIFACT_KEY = "work-catalogue"
SOURCE_ENTITY_KIND = "SYSTEM"
SOURCE_ENTITY_KEY = "docplane-work"
SECTION = "work"

_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
_QUEUE_STATES = ("ACTIVE", "BACKLOG", "BLOCKED", "SOAKING", "PARKED")
_QUEUE_PAGES = {
    "ACTIVE": ("now", "Now", "Being worked right now. The WIP limit keeps this list honest."),
    "BACKLOG": ("roadmap", "Roadmap", "Decided but not started — ranked by priority. Sprint candidates come from here."),
    "BLOCKED": ("blocked", "Blocked", "Waiting on something named. Each entry says what."),
    "SOAKING": ("soaking", "Soaking", "Done and under observation. Soak criteria reference real monitoring."),
    "PARKED": ("parked", "Parked", "Someday/maybe, each with a review date so parking is never forever."),
}
_COMPLETED_LIMIT = 20


def _key(fingerprint: str, verb: str, discriminator: str = "") -> str:
    suffix = f":{discriminator}" if discriminator else ""
    return f"work-catalogue:{fingerprint}:{verb}{suffix}"


def fetch_state(client: Client) -> dict[str, Any]:
    """One deterministic projection of the work domain, via the public API."""
    queues = client.call("GET", "/api/v1/work/queues")
    listing = client.call("GET", "/api/v1/initiatives?include_closed=true&limit=500")
    initiatives = listing.get("initiatives", [])
    details: dict[str, dict[str, Any]] = {}
    for initiative in initiatives:
        if initiative.get("work_state") in _QUEUE_STATES:
            details[initiative["initiative_id"]] = client.call(
                "GET", f"/api/v1/initiatives/{initiative['initiative_id']}"
            )
    return {
        "wip_limit": queues.get("wip_limit"),
        "inbox_count": queues.get("inbox", 0),
        "initiatives": initiatives,
        "details": details,
    }


def _projection(state: dict[str, Any]) -> dict[str, Any]:
    """The exact facts that render — sorted, stripped of volatile identifiers
    that do not change page content."""
    rows = []
    for initiative in sorted(state["initiatives"], key=lambda item: str(item.get("initiative_key") or "")):
        detail = state["details"].get(initiative["initiative_id"], {})
        rows.append({
            "key": initiative.get("initiative_key"),
            "title": initiative.get("title"),
            "state": initiative.get("work_state"),
            "priority": initiative.get("priority"),
            "objective": initiative.get("objective"),
            "target_date": str(initiative.get("target_date") or ""),
            "review_due_at": str(initiative.get("review_due_at") or ""),
            "blocker_summary": initiative.get("blocker_summary"),
            "soak": {
                field: str(initiative.get(field) or "")
                for field in ("soak_started_at", "soak_review_at", "soak_success_criteria",
                              "soak_failure_conditions", "soak_monitoring_ref")
            },
            "parked": {
                "reason": initiative.get("parked_reason"),
                "review_at": str(initiative.get("parked_review_at") or ""),
                "indefinitely": bool(initiative.get("parked_indefinitely")),
            },
            "completed_at": str(initiative.get("completed_at") or ""),
            "dispositions": {
                "know": initiative.get("promotion_state"),
                "model": initiative.get("model_disposition"),
                "observe": initiative.get("observe_disposition"),
            },
            "activities": [
                {"type": activity.get("activity_type"), "body": activity.get("body"),
                 "at": str(activity.get("created_at") or "")}
                for activity in detail.get("activities", [])
            ],
            "links": [
                {"relation": link.get("relation"), "resource_type": link.get("resource_type"),
                 "resource_id": link.get("resource_id")}
                for link in detail.get("links", [])
            ],
        })
    return {"wip_limit": state["wip_limit"], "inbox_count": state["inbox_count"], "initiatives": rows}


def fingerprint(state: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_projection(state), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required; refusing to fall back to another principal")
    return value


def _write_metrics(path: str, *, drift: bool, success: bool) -> None:
    """Atomically publish low-cardinality textfile metrics for node_exporter."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    content = (
        "# HELP docplane_generated_projection_drift Whether live source state differs from the published generation fingerprint.\n"
        "# TYPE docplane_generated_projection_drift gauge\n"
        f'docplane_generated_projection_drift{{artifact="{ARTIFACT_KEY}"}} {int(drift)}\n'
        "# HELP docplane_generated_projection_reconcile_success Whether the most recent reconciliation completed successfully.\n"
        "# TYPE docplane_generated_projection_reconcile_success gauge\n"
        f'docplane_generated_projection_reconcile_success{{artifact="{ARTIFACT_KEY}"}} {int(success)}\n'
        "# HELP docplane_generated_projection_last_run_unixtime Unix time of the most recent reconciliation status check.\n"
        "# TYPE docplane_generated_projection_last_run_unixtime gauge\n"
        f'docplane_generated_projection_last_run_unixtime{{artifact="{ARTIFACT_KEY}"}} {now}\n'
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(0o644)
    temporary.replace(destination)


def _by_state(projection: dict[str, Any], work_state: str) -> list[dict[str, Any]]:
    rows = [row for row in projection["initiatives"] if row["state"] == work_state]
    return sorted(rows, key=lambda row: (_PRIORITY_ORDER.get(row["priority"], 9), str(row["key"])))


def _footer(fp: str) -> list[str]:
    return [
        "",
        "---",
        "",
        f"*Generated by {GENERATOR_NAME} v{GENERATOR_VERSION} · work-state fingerprint "
        f"`{fp[:16]}` · read-only rendering — [act on this in the dashboard](/dashboard/#work).*",
        "",
    ]


def _row_line(row: dict[str, Any]) -> str:
    extras = []
    if row.get("target_date"):
        extras.append(f"target {row['target_date'][:10]}")
    if row.get("review_due_at"):
        extras.append(f"review {row['review_due_at'][:10]}")
    meta = f" · {' · '.join(extras)}" if extras else ""
    objective = (row.get("objective") or "").strip().splitlines()[0] if row.get("objective") else ""
    return (
        f"| [{row['key']}](initiatives/{row['key']}.md) | {row['title']} | "
        f"{row['priority']} | {objective}{meta} |"
    )


def _queue_page(projection: dict[str, Any], work_state: str, fp: str) -> dict[str, str]:
    slug, label, blurb = _QUEUE_PAGES[work_state]
    rows = _by_state(projection, work_state)
    lines = [f"# {label}", "", blurb, ""]
    if work_state == "ACTIVE" and projection.get("wip_limit"):
        lines += [f"WIP limit: **{len(rows)}/{projection['wip_limit']}**.", ""]
    if rows:
        lines += ["| Initiative | Title | Priority | Objective |", "|---|---|---|---|"]
        lines += [_row_line(row) for row in rows]
    else:
        lines += ["Nothing here right now."]
    if work_state == "BLOCKED":
        for row in rows:
            if row.get("blocker_summary"):
                lines += ["", f"**{row['key']}** is blocked on: {row['blocker_summary']}"]
    if work_state == "SOAKING":
        for row in rows:
            soak = row["soak"]
            lines += ["", f"## {row['key']}", ""]
            if soak.get("soak_started_at"):
                lines += [f"- Soaking since {soak['soak_started_at'][:10]}, review {str(soak.get('soak_review_at') or '')[:10]}"]
            if soak.get("soak_monitoring_ref"):
                lines += [f"- Watched by: `{soak['soak_monitoring_ref']}`"]
            if soak.get("soak_success_criteria"):
                lines += [f"- Success: {soak['soak_success_criteria']}"]
            if soak.get("soak_failure_conditions"):
                lines += [f"- Failure: {soak['soak_failure_conditions']}"]
    if work_state == "PARKED":
        for row in rows:
            parked = row["parked"]
            note = parked.get("reason") or ""
            when = "indefinitely" if parked.get("indefinitely") else f"review {str(parked.get('review_at') or '')[:10]}"
            lines += ["", f"**{row['key']}** — parked ({when}){': ' + note if note else ''}"]
    return {
        "path": f"{SECTION}/{slug}.md",
        "title": label,
        "nav_path": f"Work/{label}",
        "content": "\n".join(lines + _footer(fp)),
    }


def _index_page(projection: dict[str, Any], fp: str) -> dict[str, str]:
    counts = {state: len(_by_state(projection, state)) for state in _QUEUE_STATES}
    completed = [row for row in projection["initiatives"] if row["state"] == "COMPLETE"]
    lines = [
        "# Work",
        "",
        "The work domain, rendered for browsing: what is moving, what is decided,",
        "what is waiting, and what is under observation. Everything here is",
        "generated from live initiative state — to capture, triage or transition,",
        "use the [dashboard](/dashboard/#work).",
        "",
        "| Queue | Count |",
        "|---|---|",
        f"| [Now](now.md) | {counts['ACTIVE']}" + (f" / WIP {projection['wip_limit']}" if projection.get("wip_limit") else "") + " |",
        f"| [Roadmap](roadmap.md) | {counts['BACKLOG']} |",
        f"| [Blocked](blocked.md) | {counts['BLOCKED']} |",
        f"| [Soaking](soaking.md) | {counts['SOAKING']} |",
        f"| [Parked](parked.md) | {counts['PARKED']} |",
        f"| [Recently completed](recently-completed.md) | {min(len(completed), _COMPLETED_LIMIT)} |",
        "",
        f"Inbox: **{projection['inbox_count']}** untriaged capture(s) — triage happens in the dashboard;",
        "pre-triage thoughts are deliberately not published here.",
    ]
    return {
        "path": f"{SECTION}/index.md",
        "title": "Work",
        "nav_path": "Work/Overview",
        "content": "\n".join(lines + _footer(fp)),
    }


def _completed_page(projection: dict[str, Any], fp: str) -> dict[str, str]:
    rows = sorted(
        (row for row in projection["initiatives"] if row["state"] == "COMPLETE"),
        key=lambda row: str(row.get("completed_at") or ""),
        reverse=True,
    )[:_COMPLETED_LIMIT]
    lines = ["# Recently completed", ""]
    if rows:
        lines += ["| Initiative | Title | Completed | know / model / observe |", "|---|---|---|---|"]
        for row in rows:
            dispositions = row["dispositions"]
            gate = " / ".join(str(dispositions.get(domain) or "—") for domain in ("know", "model", "observe"))
            lines += [f"| {row['key']} | {row['title']} | {str(row.get('completed_at') or '')[:10]} | {gate} |"]
        lines += ["", "The three-column gate shows each closure disposition — a `DEFERRED` is a visible gap, not a pass."]
    else:
        lines += ["Nothing completed yet."]
    return {
        "path": f"{SECTION}/recently-completed.md",
        "title": "Recently completed",
        "nav_path": "Work/Recently completed",
        "content": "\n".join(lines + _footer(fp)),
    }


def _initiative_page(row: dict[str, Any], fp: str) -> dict[str, str]:
    lines = [
        f"# {row['title']}",
        "",
        f"**{row['key']}** · {row['state']} · priority {row['priority']}",
        "",
        row.get("objective") or "",
    ]
    if row.get("blocker_summary"):
        lines += ["", f"**Blocked on:** {row['blocker_summary']}"]
    soak = row.get("soak") or {}
    if row["state"] == "SOAKING" and soak.get("soak_success_criteria"):
        lines += ["", "## Soak", "", f"- Success: {soak['soak_success_criteria']}"]
        if soak.get("soak_failure_conditions"):
            lines += [f"- Failure: {soak['soak_failure_conditions']}"]
        if soak.get("soak_monitoring_ref"):
            lines += [f"- Watched by: `{soak['soak_monitoring_ref']}`"]
    if row.get("activities"):
        lines += ["", "## Activity", ""]
        lines += [f"- {activity['at'][:10]} · {activity['type']}: {activity['body']}" for activity in row["activities"]]
    if row.get("links"):
        lines += ["", "## Linked", ""]
        lines += [f"- {link['relation']} → {link['resource_type']} `{link['resource_id']}`" for link in row["links"]]
    return {
        "path": f"{SECTION}/initiatives/{row['key']}.md",
        "title": row["title"],
        "nav_path": f"Work/Initiatives/{row['title']}",
        "content": "\n".join(lines + _footer(fp)),
    }


def render_pages(state: dict[str, Any], fp: str) -> list[dict[str, str]]:
    projection = _projection(state)
    pages = [_index_page(projection, fp)]
    pages += [_queue_page(projection, work_state, fp) for work_state in _QUEUE_STATES]
    pages += [_completed_page(projection, fp)]
    pages += [
        _initiative_page(row, fp)
        for row in projection["initiatives"]
        if row["state"] in _QUEUE_STATES
    ]
    return pages


def ensure_source_entity(client: Client, fp: str) -> str:
    listing = client.call("GET", f"/api/v1/model/entities?entity_kind={SOURCE_ENTITY_KIND}&limit=1000")
    for entity in listing.get("entities", []):
        if entity["entity_key"] == SOURCE_ENTITY_KEY:
            return entity["entity_id"]
    created = client.call(
        "POST", "/api/v1/model/entities",
        {
            "entity_kind": SOURCE_ENTITY_KIND,
            "entity_key": SOURCE_ENTITY_KEY,
            "display_name": "DocPlane work domain",
            "attributes": {"description": "Source of the generated work catalogue"},
        },
        _key(fp, "source-entity"),
    )
    return created["entity_id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="render and report; perform no writes")
    parser.add_argument("--status-json", action="store_true", help="report live-versus-published fingerprint state; perform no writes")
    parser.add_argument("--metrics-file", help="atomically write projection status in Prometheus textfile format")
    parser.add_argument("--reconcile-success", choices=("0", "1"), default="1", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    client = Client(_required_environment("DOCPLANE_API"), _required_environment("DOCPLANE_WORK_CATALOGUE_TOKEN"))
    state = fetch_state(client)
    fp = fingerprint(state)
    pages = render_pages(state, fp)
    open_count = sum(1 for row in state["initiatives"] if row.get("work_state") in _QUEUE_STATES)
    if args.status_json or args.metrics_file:
        artifact = sc.current_artifact(client, ARTIFACT_KEY)
        published = sc.last_generation_fingerprint(client, artifact["artifact_id"]) if artifact else None
        drift = published != fp
        status = {
            "artifact_key": ARTIFACT_KEY,
            "artifact_id": artifact.get("artifact_id") if artifact else None,
            "live_fingerprint": fp,
            "published_fingerprint": published,
            "drift": drift,
            "reconcile_success": args.reconcile_success == "1",
        }
        if args.metrics_file:
            _write_metrics(args.metrics_file, drift=drift, success=args.reconcile_success == "1")
        if args.status_json:
            print(json.dumps(status, sort_keys=True))
        return 0
    print(f"work fingerprint {fp[:16]} — {open_count} open initiative(s), {len(pages)} page(s)")

    if args.dry_run:
        for page in pages:
            print(f"DRY-RUN would publish {page['path']} ({len(page['content'])} bytes)")
        return 0

    artifact = sc.current_artifact(client, ARTIFACT_KEY)

    def observe_generation(current: dict[str, Any]) -> None:
        client.call(
            "POST", "/api/v1/observations",
            {
                "observations": [
                    {
                        "subject_artifact_id": current["artifact_id"],
                        "observation_kind": "GENERATION",
                        "outcome": "NOMINAL",
                        "source_fingerprint": fp,
                        "summary": f"Rendered the work catalogue: {open_count} open initiative(s)",
                        "idempotency_key": _key(fp, "generation"),
                    }
                ]
            },
            _key(fp, "observation-batch"),
        )

    desired_paths = sorted(page["path"] for page in pages)
    if artifact is not None:
        previous = sc.last_generation_fingerprint(client, artifact["artifact_id"])
        if previous == fp and not needs_succession(artifact, desired_paths):
            observe_generation(artifact)
            print(f"UNCHANGED {fp[:16]} — nothing to regenerate")
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
    # Initiative pages the previous declaration owned but that no longer
    # render (initiative closed or renamed) are archived in the same change.
    stale_paths = sorted(set((artifact or {}).get("target_page_paths") or []) - set(desired_paths))
    for path in stale_paths:
        current = lookup(path)
        if current is not None:
            operations.append(("ARCHIVE_PAGE", current["resource_id"], current["revision"], {"path": path}))

    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": f"Work catalogue regeneration {fp[:16]}",
            "purpose": f"Fingerprint-bound regeneration by the work-catalogue generator; work-state fingerprint {fp}.",
            "workspace_key": "reference",
        },
        _key(fp, "change"),
    )
    change_id = change["change_id"]
    for operation_type, resource_id, revision, page in operations:
        request: dict[str, Any] = {"operation_type": operation_type, "payload": {}}
        if operation_type != "ARCHIVE_PAGE":
            request["payload"] = {
                "path": page["path"], "title": page["title"], "nav_path": page["nav_path"],
                "content": page["content"], "knowledge_class": "REFERENCE",
            }
        if resource_id:
            request["page_resource_id"] = resource_id
            request["expected_revision"] = revision
        client.call("POST", f"/api/v1/changes/{change_id}/operations", request, _key(fp, "operation", page["path"]))
    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(fp, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(fp, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        raise RuntimeError(f"publication deployment reported {deployment.get('status')}")

    page_ids: dict[str, str] = {}
    for page in pages:
        found = lookup(page["path"])
        if found is None:
            raise RuntimeError(f"published page not found after publication: {page['path']}")
        page_ids[page["path"]] = found["resource_id"]

    if artifact is not None and needs_succession(artifact, sorted(page_ids)):
        client.call(
            "POST", f"/api/v1/model/artifacts/{artifact['artifact_id']}/retire",
            {"expected_version": artifact["version"], "note": f"superseded by work state {fp[:16]}"},
            _key(fp, "artifact-retire"),
        )
        print(f"RETIRED artifact {artifact['artifact_id']} — target set or generator contract moved")
        artifact = None
    if artifact is None:
        source_id = ensure_source_entity(client, fp)
        artifact = client.call(
            "POST", "/api/v1/model/artifacts",
            {
                "artifact_key": ARTIFACT_KEY,
                "generator_name": GENERATOR_NAME,
                "generator_version": GENERATOR_VERSION,
                "source_entity_id": source_id,
                "redaction_policy": "canonical",
                "target_page_resource_ids": sorted(page_ids.values()),
                "target_page_paths": sorted(page_ids),
            },
            _key(fp, "artifact"),
        )
    observe_generation(artifact)
    print(f"PUBLISHED {len(pages)} page(s), archived {len(stale_paths)} stale path(s), fingerprint {fp[:16]}")
    return 0


def needs_succession(artifact: dict[str, Any], desired_paths: list[str]) -> bool:
    return (
        sorted(artifact.get("target_page_paths") or []) != sorted(desired_paths)
        or artifact.get("generator_version") != GENERATOR_VERSION
    )


if __name__ == "__main__":
    sys.exit(main())
