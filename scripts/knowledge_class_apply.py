#!/usr/bin/env python3
"""Apply an operator-curated knowledge-class report through DocPlane trust.

The input is ``knowledge-class-suggestions-v1`` JSON produced by
``knowledge_class_suggest.py`` and curated by an operator.  This driver never
writes SQL: every mutation is the existing receipt-replayed classify verb,
which records metadata history and PAGE_CLASSIFIED audit events.

Environment only (bearers are never accepted on argv):
  DOCPLANE_API
  DOCPLANE_TOKEN
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from knowledge_class_suggest import CONTRACT_VERSION, KNOWLEDGE_CLASSES  # noqa: E402
from schema_catalogue import ApiError, Client  # noqa: E402

DEFAULT_BATCH_LIMIT = 50


def load_curated(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read curated report: {error}") from error
    if report.get("contract_version") != CONTRACT_VERSION:
        raise ValueError(f"contract_version must be {CONTRACT_VERSION!r}")
    suggestions = report.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("suggestions must be a list")
    unknown = sorted({
        str(item.get("proposed") if isinstance(item, dict) else item)
        for item in suggestions
        if not isinstance(item, dict) or item.get("proposed") not in KNOWLEDGE_CLASSES
    })
    if unknown:
        raise ValueError(f"unknown knowledge class(es): {', '.join(unknown)}")
    for index, item in enumerate(suggestions):
        if not isinstance(item, dict):
            raise ValueError(f"suggestions[{index}] must be an object")
        if not item.get("resource_id") and not item.get("path"):
            raise ValueError(f"suggestions[{index}] needs resource_id or path")
    return report


def _resolve_page(client: Client, entry: dict[str, Any]) -> dict[str, Any]:
    resource_id = entry.get("resource_id")
    if not resource_id:
        from urllib.parse import urlencode

        listing = client.call("GET", "/api/v1/pages?" + urlencode({"status": "all", "path": entry["path"], "limit": 2}))
        pages = listing.get("pages", [])
        if len(pages) != 1:
            raise ValueError(f"path does not resolve uniquely: {entry['path']}")
        resource_id = pages[0]["resource_id"]
    return client.call("GET", f"/api/v1/pages/{resource_id}/trust")


def _idempotency_key(report: dict[str, Any], page: dict[str, Any], proposed: str, expected_version: int) -> str:
    logical = f"{report.get('corpus_stamp', 'curated')}:{page['resource_id']}:{proposed}:{expected_version}"
    return f"knowledge-class-apply-v1-{hashlib.sha256(logical.encode()).hexdigest()[:32]}"


def apply_report(client: Client, report: dict[str, Any], batch_limit: int = DEFAULT_BATCH_LIMIT) -> dict[str, Any]:
    """Apply at most ``batch_limit`` curated entries in report order.

    Matching pages converge without a write. A class changed after curation is
    listed as a skip. Races and other metadata movement are refused by the
    classify verb's expected_metadata_version lock and surfaced as skips.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be at least 1")
    entries = report["suggestions"]
    result: dict[str, Any] = {
        "contract_version": "knowledge-class-apply-v1",
        "curated_entries": len(entries),
        "batch_limit": batch_limit,
        "considered": 0,
        "applied": [],
        "already_matched": [],
        "optimistic_lock_skips": [],
        "remaining": 0,
    }
    write_attempts = 0
    for index, entry in enumerate(entries):
        if write_attempts >= batch_limit:
            result["remaining"] = len(entries) - index
            break
        result["considered"] += 1
        page = _resolve_page(client, entry)
        proposed = entry["proposed"]
        current = page.get("knowledge_class")
        identity = {"resource_id": page["resource_id"], "path": page["path"], "proposed": proposed}
        if current == proposed:
            result["already_matched"].append(identity)
            continue
        if current is not None:
            result["optimistic_lock_skips"].append({
                **identity,
                "code": "KNOWLEDGE_CLASS_CHANGED_SINCE_CURATION",
                "current": current,
            })
            continue
        expected_version = int(entry.get("metadata_version") or page["metadata_version"])
        payload = {
            "expected_metadata_version": expected_version,
            "workspace_id": page["workspace_id"],
            "publication_state": page["publication_state"],
            "knowledge_class": proposed,
            "criticality": page["criticality"],
            "owner_principal_id": page.get("owner_principal_id"),
            "review_due_at": page.get("review_due_at"),
            "provenance": page.get("provenance"),
            "reason": f"Applied operator-curated knowledge-class report to {page['path']}",
        }
        try:
            write_attempts += 1
            client.call(
                "POST",
                f"/api/v1/pages/{page['resource_id']}/classification",
                payload,
                _idempotency_key(report, page, proposed, expected_version),
            )
        except ApiError as error:
            if error.status not in {409, 412}:
                raise
            detail = error.body.get("detail", error.body) if isinstance(error.body, dict) else error.body
            result["optimistic_lock_skips"].append({**identity, "code": "OPTIMISTIC_LOCK_REFUSED", "detail": detail})
            continue
        result["applied"].append(identity)
    result["counts"] = {
        "applied": len(result["applied"]),
        "already_matched": len(result["already_matched"]),
        "optimistic_lock_skips": len(result["optimistic_lock_skips"]),
        "remaining": result["remaining"],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="operator-curated suggestions JSON")
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    args = parser.parse_args(argv)
    try:
        report = load_curated(args.report)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    api = os.environ.get("DOCPLANE_API")
    token = os.environ.get("DOCPLANE_TOKEN")
    if not api or not token:
        print("set DOCPLANE_API and DOCPLANE_TOKEN in the environment", file=sys.stderr)
        return 2
    try:
        result = apply_report(Client(api, token), report, args.batch_limit)
    except (ApiError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
