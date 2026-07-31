#!/usr/bin/env python3
"""Seed the knowledge-class example corpus into a DocPlane installation.

Publishes the nine pages in manifest.json through the normal authoring
API — one change, CREATE_PAGE operations, validate, publish — so the
result carries real receipts, audit events and page history like any
authored content. Three pages are classified at birth via
CREATE_PAGE.knowledge_class; six are deliberately left unclassified so
scripts/knowledge_class_suggest.py has something to demonstrate.

Pages that already exist are skipped, never replaced: the examples are a
starting point, not a managed artifact. Re-running against the same
installation is therefore safe and converges to "nothing to do".

Usage (credentials ride the environment, never argv):

    export DOCPLANE_API=http://localhost:8080
    export DOCPLANE_TOKEN=dp_...
    python3 examples/knowledge-classes/seed_examples.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from schema_catalogue import ApiError, Client  # noqa: E402  (shared API client)

WORKSPACE_KEY = "reference"


def load_manifest() -> tuple[dict[str, Any], str]:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    digest.update(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    for page in manifest["pages"]:
        digest.update((HERE / page["file"]).read_bytes())
    return manifest, digest.hexdigest()


def _key(stamp: str, verb: str, discriminator: str = "") -> str:
    return (
        f"kc-examples-{stamp[:16]}-{verb}"
        f"{'-' + discriminator.replace('/', '-') if discriminator else ''}"
    )[:256]


def main() -> int:
    api = os.environ.get("DOCPLANE_API")
    token = os.environ.get("DOCPLANE_TOKEN")
    if not api or not token:
        print("Set DOCPLANE_API and DOCPLANE_TOKEN in the environment.", file=sys.stderr)
        return 2

    client = Client(api, token)
    manifest, stamp = load_manifest()

    wanted: list[dict[str, Any]] = []
    skipped: list[str] = []
    for page in manifest["pages"]:
        found = client.call("GET", f"/api/v1/pages?path={page['path']}&status=all").get("pages", [])
        (skipped if found else wanted).append(page if not found else page["path"])
    if not wanted:
        print(json.dumps({"created": [], "skipped_existing": skipped, "note": "nothing to do"}, indent=2))
        return 0

    change = client.call(
        "POST", "/api/v1/changes",
        {
            "title": "Seed knowledge-class example corpus",
            "purpose": (
                "Publish the generic example pages from examples/knowledge-classes/ "
                f"(manifest stamp {stamp[:16]}) demonstrating the eight knowledge "
                "classes and the suggest/curate/apply workflow."
            ),
            "workspace_key": WORKSPACE_KEY,
        },
        _key(stamp, "change"),
    )
    change_id = change["change_id"]

    for page in wanted:
        payload: dict[str, Any] = {
            "path": page["path"],
            "title": page["title"],
            "nav_path": page["nav_path"],
            "content": (HERE / page["file"]).read_text(encoding="utf-8"),
        }
        if page["knowledge_class"]:
            payload["knowledge_class"] = page["knowledge_class"]
        client.call(
            "POST", f"/api/v1/changes/{change_id}/operations",
            {"operation_type": "CREATE_PAGE", "payload": payload},
            _key(stamp, "operation", page["path"]),
        )

    client.call("POST", f"/api/v1/changes/{change_id}/validate", {}, _key(stamp, "validate"))
    receipt = client.call("POST", f"/api/v1/changes/{change_id}/publish", {}, _key(stamp, "publish"))
    deployment = (receipt.get("publication_receipt") or receipt).get("deployment", {})
    if deployment.get("status") not in {"COMPLETED", None}:
        print(f"publication deployment reported {deployment.get('status')}", file=sys.stderr)
        return 1

    print(json.dumps(
        {
            "created": [
                {
                    "path": page["path"],
                    "knowledge_class": page["knowledge_class"],
                    "demonstrates": page["demonstrates"],
                }
                for page in wanted
            ],
            "skipped_existing": skipped,
            "next": "run scripts/knowledge_class_suggest.py to see the unclassified pages proposed",
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
